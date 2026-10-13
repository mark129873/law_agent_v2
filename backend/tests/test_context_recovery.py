"""逐步预算与明确超限恢复：假模型、假工具，不调用外部服务。"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from anthropic import BadRequestError

from app.agent import compact, hooks
from app.agent.loop import TurnDeps, run_turn
from app.config import settings
from app.sessions import replay, store
from app.sessions.recorder import TurnRecorder
from storage_seed import compactions


def config():
    return SimpleNamespace(context_window=1200, compact_output_reserve=100,
                           compact_buffer=100, microcompact_keep=5, data_dir=settings.data_dir)


def overflow(message="prompt is too long", code="invalid_request_error"):
    """构造SDK真实异常类型；没有任何HTTP请求。"""
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.invalid/messages"))
    return BadRequestError(message, response=response, body={"error": {"type": code, "message": message}})


def prepare(store_db, client, *, prior=True, request="当前请求必须保留"):
    if prior:
        store.ensure_session(store_db, "budget", "fake", "旧轮")
        store.save_user_message(store_db, "budget", "old-user", "x" * 2400, "old-turn")
        store.upsert_message(store_db, "budget", "old-answer", "assistant", {}, "old-turn")
        store.upsert_part(store_db, "budget", "old-answer", "old-text", "text", {"text": "旧轮已完成"}, "old-turn")
    history = replay.load_history(store_db, "budget") + [{"role": "user", "content": request}]
    compact.reset_failure_count("budget")
    return TurnDeps(client=client, recorder=TurnRecorder(store_db, "budget", model="fake", max_tokens=100),
                    history=history, system_prompt="fake", settings=config())


def run(deps):
    async def scenario():
        return [event async for event in run_turn(deps)]
    return asyncio.run(scenario())


def test_tool_growth_compacts_only_prior_prefix(store_db, monkeypatch):
    """第一步预算内，两个结果增长后触发；保留用户原文、全部配对、存储输出及边界。"""
    executed = []
    output = "完整工具结果" + "y" * 1600
    monkeypatch.setattr("app.agent.loop.execute_tool", lambda name, args: executed.append(args) or output)

    class Fake:
        calls = summaries = 0
        async def stream(self, *, system, messages, tools):
            if not tools:
                self.summaries += 1
                transcript = messages[0]["content"]
                assert "当前请求必须保留" not in transcript
                assert "完整工具结果" not in transcript
                yield {"type": "text_delta", "text": "旧轮已完成摘要"}
                return
            self.calls += 1
            if self.calls == 1:
                for call in ("one", "two"):
                    yield {"type": "tool_use", "id": call, "name": "read_file", "input": {"path": call}}
                yield {"type": "usage", "input_tokens": 610, "output_tokens": 20}
            else:
                assert messages[1] == {"role": "user", "content": "当前请求必须保留"}
                assert [item["id"] for item in messages[-2]["content"]] == ["one", "two"]
                assert [item["tool_use_id"] for item in messages[-1]["content"]] == ["one", "two"]
                assert all(item["content"] == output for item in messages[-1]["content"])
                yield {"type": "text_delta", "text": "完成"}

    fake = Fake()
    deps = prepare(store_db, fake)
    events = run(deps)
    assert events[-1]["state"] == "success"
    assert fake.calls == 2 and fake.summaries == 1 and len(executed) == 2
    assert any(event["type"] == "compacted" for event in events)
    assert store._load(compactions(store_db, "budget")[-1].data)["tail_start_id"] == "old-answer"
    assert output in str(replay.load_history(store_db, "budget"))


@pytest.mark.parametrize("error", [overflow(), overflow("long", "context_length_exceeded")])
def test_actual_context_error_recovers_once(store_db, error):
    """估算未超限但服务端拒绝时强制摘要，再调用；失败请求记入模型步。"""
    class Fake:
        calls = summaries = 0
        async def stream(self, *, system, messages, tools):
            if not tools:
                self.summaries += 1
                yield {"type": "text_delta", "text": "旧轮摘要"}
            else:
                self.calls += 1
                if self.calls == 1:
                    raise error
                assert messages[-1]["content"] == "当前请求必须保留"
                yield {"type": "text_delta", "text": "已恢复"}
    fake = Fake()
    events = run(prepare(store_db, fake))
    assert events[-1]["state"] == "success"
    assert fake.calls == 2 and fake.summaries == 1
    assert not any(event["type"] == "error" for event in events)


@pytest.mark.parametrize("kind", ["ordinary400", "network", "output_limit", "repeated", "empty", "no_prior", "partial"])
def test_recovery_has_strict_failure_boundaries(store_db, kind):
    """普通错误不重试；连续超限/无历史/空摘要/部分输出也不能无限恢复。"""
    class Fake:
        calls = summaries = 0
        async def stream(self, *, system, messages, tools):
            if not tools:
                self.summaries += 1
                yield {"type": "text_delta", "text": " \n " if kind == "empty" else "旧轮摘要"}
                return
            self.calls += 1
            if kind == "ordinary400":
                raise overflow("invalid tool schema")
            if kind == "network":
                raise ConnectionError("connection lost")
            if kind == "output_limit":
                raise overflow("max_tokens exceeds output limit")
            if kind == "partial":
                yield {"type": "text_delta", "text": "已经输出"}
            raise overflow()
    fake = Fake()
    deps = prepare(store_db, fake, prior=kind != "no_prior")
    before = deepcopy(deps.history)
    events = run(deps)
    assert events[-1]["state"] == "failed"
    assert fake.calls == (2 if kind == "repeated" else 1)
    assert fake.summaries == (1 if kind in {"repeated", "empty"} else 0)
    if kind != "repeated":
        assert deps.history == before
        assert not compactions(store_db, "budget")
    if kind in {"repeated", "empty", "no_prior"}:
        assert "上下文超限" in next(event["message"] for event in events if event["type"] == "error")


@pytest.mark.parametrize("giant_request", [True, False])
def test_current_turn_too_large_fails_without_truncation(store_db, monkeypatch, giant_request):
    """没有安全旧前缀时交还；当前大请求/工具结果保持原文，工具只执行一次。"""
    executed = []
    output = "y" * 5000
    monkeypatch.setattr("app.agent.loop.execute_tool", lambda *args: executed.append(args) or output)
    class Fake:
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            yield {"type": "tool_use", "id": "only", "name": "read_file", "input": {"path": "a"}}
    fake = Fake()
    request = "q" * 5000 if giant_request else "当前请求"
    deps = prepare(store_db, fake, prior=False, request=request)
    events = run(deps)
    assert events[-1]["state"] == "failed"
    assert "无法安全压缩" in next(event["message"] for event in events if event["type"] == "error")
    assert fake.calls == (0 if giant_request else 1)
    assert len(executed) == (0 if giant_request else 1)
    assert deps.history[0]["content"] == request
    # 首轮本身过大与工具增长两种前置失败都持久化原因，重新打开会话仍可重试。
    restored = replay.load_replay(store_db, "budget")["turns"][-1]
    assert restored["state"] == "failed"
    errors = [item["message"] for item in restored["work_items"] if item["kind"] == "error"]
    assert len(errors) == 1 and "无法安全压缩" in errors[0]
    if not giant_request:
        assert deps.history[-1]["content"][0]["content"] == output
        assert output in str(replay.load_history(store_db, "budget"))


def test_recovery_does_not_reset_40_step_limit(store_db, monkeypatch):
    """一次失败+39次有效响应即40步，Stop续轮与恢复不能重置总计数。"""
    monkeypatch.setattr(hooks, "trigger", lambda event, data: "继续" if event == "Stop" else None)
    class Fake:
        calls = summaries = 0
        async def stream(self, *, system, messages, tools):
            if not tools:
                self.summaries += 1
                yield {"type": "text_delta", "text": "旧轮摘要"}
            else:
                self.calls += 1
                if self.calls == 1:
                    raise overflow()
                yield {"type": "text_delta", "text": "继续"}
    fake = Fake()
    deps = prepare(store_db, fake)
    events = run(deps)
    assert fake.calls == 40 and fake.summaries == 1
    assert events[-1]["state"] == "failed"
    assert any(event.get("message") == "达到单轮步数上限 40" for event in events)
    assert sum(item.get("content") == "继续" for item in deps.history) == 78


def test_measured_usage_accounts_for_new_tool_growth():
    """旧usage较小时，模型步后新增输出依然触发预算；usage基线偏高也不漏增量。"""
    history = [{"role": "user", "content": "x" * 2000}]
    assert compact.needs_compact(config(), history, 900, 300)
    assert compact.needs_compact(config(), [{"role": "user", "content": "x" * 5000}], 1, 10)


def test_context_recovery_after_tool_does_not_repeat_side_effect(store_db, monkeypatch):
    """工具已完成后模型拒绝输入；仅重试模型，完整配对保留且工具不会再次执行。"""
    executed = []
    monkeypatch.setattr("app.agent.loop.execute_tool", lambda *args: executed.append(args) or "saved")
    class Fake:
        calls = summaries = 0
        async def stream(self, *, system, messages, tools):
            if not tools:
                self.summaries += 1
                yield {"type": "text_delta", "text": "旧轮摘要"}
                return
            self.calls += 1
            if self.calls == 1:
                yield {"type": "tool_use", "id": "write-once", "name": "write_file",
                       "input": {"path": "a", "content": "b"}}
            elif self.calls == 2:
                raise overflow()
            else:
                assert messages[-2]["content"][0]["id"] == "write-once"
                assert messages[-1]["content"][0] == {"type": "tool_result", "tool_use_id": "write-once", "content": "saved"}
                yield {"type": "text_delta", "text": "完成"}
    fake = Fake()
    events = run(prepare(store_db, fake))
    assert events[-1]["state"] == "success"
    assert fake.calls == 3 and fake.summaries == 1 and len(executed) == 1


def test_high_measured_usage_still_attempts_summary_without_micro_candidates(store_db, monkeypatch):
    """system/tools开销使真实输入高于字符估算；micro没有改动时不能丢掉该依据。"""
    monkeypatch.setattr("app.agent.loop.execute_tool", lambda *args: "小输出")
    class Fake:
        calls = summaries = 0
        async def stream(self, *, system, messages, tools):
            if not tools:
                self.summaries += 1
                yield {"type": "text_delta", "text": "旧轮摘要"}
                return
            self.calls += 1
            if self.calls == 1:
                yield {"type": "tool_use", "id": "read", "name": "read_file", "input": {"path": "a"}}
                yield {"type": "usage", "input_tokens": 1100, "output_tokens": 10}
            else:
                assert "此前对话摘要" in messages[0]["content"]
                yield {"type": "text_delta", "text": "完成"}
    fake = Fake()
    events = run(prepare(store_db, fake))
    assert events[-1]["state"] == "success"
    assert fake.calls == 2 and fake.summaries == 1


def test_model_record_entry_failure_does_not_mask_original_error(store_db, monkeypatch):
    """上下文管理器在yield reply前失败也正常收口，不能再抛UnboundLocalError。"""
    from contextlib import contextmanager
    @contextmanager
    def broken_record(*args, **kwargs):
        raise overflow("prompt is too long (record entry)")
        yield
    monkeypatch.setattr("app.agent.loop.record_model_step", broken_record)
    class Unused:
        async def stream(self, **kwargs):
            raise AssertionError("记录器进入失败后不能调用模型")
            yield
    events = run(prepare(store_db, Unused()))
    assert events[-1]["state"] == "failed"
    error = next(event["message"] for event in events if event["type"] == "error")
    assert "record entry" in error
    assert "local variable" not in error
    assert not compactions(store_db, "budget")
