"""BE-7 验证：ZCode compact + microcompact（预算 / 占位改写 / 摘要边界 / 熔断）。"""

import asyncio
import json

import pytest

from app.agent import compact
from app.agent.compact import (
    context_budget,
    estimate_history_tokens,
    microcompact_parts,
    needs_compact,
)
from app.sessions import replay, store


class SettingsStub:
    """预算参数桩（不依赖 .env）。"""

    context_window = 1000
    compact_output_reserve = 320
    compact_buffer = 130

    microcompact_keep = 2


class SummaryClient:
    """假摘要客户端：吐固定摘要文本。"""

    def __init__(self, text="摘要：之前聊了压缩测试") -> None:
        self.text = text
        self.calls = 0

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        yield {"type": "text_delta", "text": self.text}


class BoomSummaryClient:
    """一直失败的摘要客户端。"""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        raise RuntimeError("摘要服务不可用")
        yield  # pragma: no cover


def _budget_stub() -> SettingsStub:
    return SettingsStub()


# ---------- 预算与判定 ----------


def test_context_budget() -> None:
    """预算 = 窗口 − 输出预留 − 缓冲。"""
    assert context_budget(_budget_stub()) == 1000 - 320 - 130


def test_needs_compact_decision() -> None:
    """真实 usage 优先，估算兜底；未超预算不压缩。"""
    small = [{"role": "user", "content": "hi"}]
    assert needs_compact(_budget_stub(), small, last_input_tokens=None) is False
    # 真实 usage 超预算 → 压缩
    assert needs_compact(_budget_stub(), small, last_input_tokens=9999) is True
    # 估算超预算（约 600 中文字符 / 4 ≈ 150？不够；用超长文本）
    big_text = "x" * 4000  # 4000/4 = 1000 > 550
    big = [{"role": "user", "content": big_text}]
    assert estimate_history_tokens(big) > context_budget(_budget_stub())
    assert needs_compact(_budget_stub(), big) is True


# ---------- microcompact ----------


def test_microcompact_keeps_recent(store_db) -> None:
    """占位改写：只清最旧的，保留最近 keep 条完整。"""
    store.ensure_session(store_db, "s1", "test-model", "压缩测试")
    store.upsert_message(store_db, "s1", "a1", "assistant", {"text": ""}, "t1")
    # 造 4 条已完成的工具输出，间隔 1ms 保证时间序
    for i in range(4):
        store.upsert_part(
            store_db, "s1", "a1", f"p{i}", "tool_call",
            {"tool_call_id": f"tc{i}", "name": "bash", "status": "completed", "output": f"输出{i}"},
            "t1",
        )
        part = store_db.get(__import__("app.models", fromlist=["Part"]).Part, f"p{i}")
        part.time_updated += i  # 人为拉开时间序

    changed = microcompact_parts(store_db, "s1", keep=2)
    assert changed == 2
    outputs = []
    for i in range(4):
        part = store_db.get(__import__("app.models", fromlist=["Part"]).Part, f"p{i}")
        outputs.append(json.loads(part.data)["output"])
    assert outputs[0].startswith("[旧工具结果已清除")
    assert outputs[1].startswith("[旧工具结果已清除")
    assert outputs[2] == "输出2" and outputs[3] == "输出3"

    # 幂等：占位符不算"有输出"，再跑一次不再改写
    assert microcompact_parts(store_db, "s1", keep=2) == 0


# ---------- 摘要边界 ----------


def _seed_long_history(store_db) -> None:
    """两轮历史 + 巨大正文（估算超预算）。"""
    store.ensure_session(store_db, "s1", "test-model", "第二轮问题")
    store.upsert_message(store_db, "s1", "u1", "user", {"text": "x" * 3000}, "t0")
    store.upsert_message(store_db, "s1", "a1", "assistant", {"text": ""}, "t0")
    store.upsert_part(store_db, "s1", "a1", "pa", "text", {"text": "y" * 3000}, "t0")


def test_summarize_and_boundary(store_db) -> None:
    """compact：写 compaction 事实；load_history 只发边界之后 + 摘要置顶。"""
    _seed_long_history(store_db)
    # 当前轮用户消息（compact 后必须保留在边界之后）
    user_row = store.upsert_message(store_db, "s1", "u2", "user", {"text": "第二轮问题"}, "t1")

    history = replay.load_history(store_db, "s1")
    history.append({"role": "user", "content": "第二轮问题"})
    prior_tokens = estimate_history_tokens(history)
    assert prior_tokens > context_budget(_budget_stub())

    async def scenario():
        deps = type("Deps", (), {
            "history": history,
            "settings": _budget_stub(),
            "last_input_tokens": None,
            "client": SummaryClient(),
            "recorder": type("R", (), {
                "session_id": "s1",
                "turn_id": "t1",
                "db": store_db,
                "first_user_sequence": user_row.sequence,
                "started_at": 1.0,
            })(),
        })()
        events = [e async for e in compact.maybe_compact(deps)]
        return events

    events = asyncio.run(scenario())
    assert len(events) == 1 and events[0]["type"] == "compacted"
    # 内存历史被就地重建：摘要 + 当前用户消息
    assert len(history) == 2
    assert "摘要：之前聊了压缩测试" in history[0]["content"]
    assert history[1] == {"role": "user", "content": "第二轮问题"}

    # 库里：compaction 事实边界 = 本轮用户消息 sequence - 1
    entries = store.list_entries(store_db, "s1", "compaction")
    data = store.entry_data(entries[-1])
    assert data["before_sequence"] == user_row.sequence - 1

    # 后续任何轮次的 load_history：摘要置顶、边界前正文不再下发
    fresh = replay.load_history(store_db, "s1")
    assert "摘要：之前聊了压缩测试" in fresh[0]["content"]
    flat = json.dumps(fresh, ensure_ascii=False)
    assert "x" * 100 not in flat  # 边界前的巨长正文不再出现
    assert {"role": "user", "content": "第二轮问题"} in fresh


def test_compact_skipped_when_within_budget(store_db) -> None:
    """未超预算：不产生 compaction 事实、不改历史。"""
    _seed_long_history(store_db)
    history = [{"role": "user", "content": "hi"}]

    async def scenario():
        deps = type("Deps", (), {
            "history": history,
            "settings": _budget_stub(),
            "last_input_tokens": None,
            "client": SummaryClient(),
            "recorder": type("R", (), {
                "session_id": "s1", "turn_id": "t1", "db": store_db,
                "first_user_sequence": 99, "started_at": 1.0,
            })(),
        })()
        return [e async for e in compact.maybe_compact(deps)]

    assert asyncio.run(scenario()) == []
    assert store.list_entries(store_db, "s1", "compaction") == []


def test_compact_circuit_breaker(store_db) -> None:
    """熔断：连续失败 3 次后跳过摘要（不再调用客户端）。"""
    _seed_long_history(store_db)
    store.upsert_message(store_db, "s1", "u2", "user", {"text": "第二轮问题"}, "t1")
    compact.reset_failure_count("s1")

    boom = BoomSummaryClient()

    async def scenario():
        for round_no in range(4):
            history = replay.load_history(store_db, "s1")
            history.append({"role": "user", "content": "第二轮问题"})
            deps = type("Deps", (), {
                "history": history,
                "settings": _budget_stub(),
                "last_input_tokens": None,
                "client": boom,
                "recorder": type("R", (), {
                    "session_id": "s1", "turn_id": f"t{round_no}", "db": store_db,
                    "first_user_sequence": 3, "started_at": 1.0,
                })(),
            })()
            events = [e async for e in compact.maybe_compact(deps)]
            assert events == []  # 失败时不吐事件，历史保持原样
        assert boom.calls == 3  # 第 4 次被熔断，不再调用

    asyncio.run(scenario())
    compact.reset_failure_count("s1")
