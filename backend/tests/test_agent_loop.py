"""BE-4 验证（主循环）：假 LLM 客户端驱动，绝不真实调用 API。

覆盖：纯文本收口 / 工具往返 / 策略拒绝 / 审批拒绝与放行 / 流中停止 / LLM 异常。
"""

import asyncio

import pytest

from app.agent.loop import TurnDeps, run_turn
from app.agent.tools import workspace_root
from app.sessions.recorder import TurnRecorder


class FakeClient:
    """假流式客户端：按预置脚本逐轮吐事件（docs/RELIABILITY.md 假 LLM 约束）。"""

    def __init__(self, steps: list[list[dict]]) -> None:
        self.steps = list(steps)
        self.calls: list[dict] = []

    async def stream(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        for event in self.steps.pop(0):
            yield event


@pytest.fixture()
def _ws(tmp_data_dir):
    """确保沙箱目录存在（真实启动由 lifespan 建，测试里手动建）。"""
    workspace_root().mkdir(parents=True, exist_ok=True)


@pytest.fixture()
def recorder(tmp_data_dir, _ws) -> TurnRecorder:
    """连到测试沙箱库的 recorder；结束自动关闭会话（防 Windows 文件锁）。"""
    from app import db

    db.init_db(tmp_data_dir)
    session = db.new_session()
    rec = TurnRecorder(session, "s1", model="test-model", max_tokens=1000)
    yield rec
    session.close()


def _run(deps) -> list[dict]:
    """同步收集全部循环事件。"""

    async def collect():
        return [event async for event in run_turn(deps)]

    return asyncio.run(collect())


def _kinds(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


# ---------- 纯文本 ----------


def test_plain_text_turn(recorder) -> None:
    """无工具：turn_started → delta → token_count → turn_completed(success)。"""
    deps = TurnDeps(
        client=FakeClient([[{"type": "text_delta", "text": "你好"}, {"type": "usage", "input_tokens": 10, "output_tokens": 5}]]),
        recorder=recorder,
        system_prompt="测试",
        history=[{"role": "user", "content": "打个招呼"}],
    )
    events = _run(deps)
    assert _kinds(events)[0] == "turn_started"
    assert _kinds(events)[-1] == "turn_completed"
    assert events[-1]["state"] == "success"
    assert {"type": "delta", "text": "你好"} in events
    assert events[-1]["active_ms"] >= 0

    # 落盘：final_text 与 turn 事实、会话 token 累计
    from app.sessions import replay

    result = replay.load_replay(deps.recorder.db, "s1")
    assert result["turns"][0]["final_text"] == "你好"
    assert result["turns"][0]["state"] == "success"
    assert result["session"]["tokens_used"] == 5


# ---------- 工具往返 ----------


def test_tool_roundtrip(recorder) -> None:
    """有工具：调用→结果回填→第二步收口；tool_use/tool_result 进历史。"""
    tool_use = {
        "type": "tool_use", "id": "tc1", "name": "write_file",
        "input": {"path": "a.txt", "content": "hello"},
    }
    client = FakeClient([
        [{"type": "text_delta", "text": "我写个文件"}, tool_use, {"type": "usage", "input_tokens": 20, "output_tokens": 8}],
        [{"type": "text_delta", "text": "写好了"}, {"type": "usage", "input_tokens": 30, "output_tokens": 6}],
    ])
    deps = TurnDeps(client=client, recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "写个文件"}])
    events = _run(deps)

    kinds = _kinds(events)
    assert kinds.count("tool_started") == 1
    done = next(e for e in events if e["type"] == "tool_completed")
    assert done["status"] == "completed" and "已写入" in done["output_preview"]
    assert kinds[-1] == "turn_completed" and events[-1]["state"] == "success"
    assert recorder.tokens_used == 14

    # 第二次调用时历史已含 tool_use 与 tool_result
    second_call_messages = client.calls[1]["messages"]
    assert any(m["role"] == "assistant" and any(b.get("type") == "tool_use" for b in m["content"])
               for m in second_call_messages)
    assert any(m["role"] == "user" and isinstance(m["content"], list)
               and m["content"][0]["type"] == "tool_result" for m in second_call_messages)

    # 文件真的写进了沙箱
    assert (workspace_root() / "a.txt").read_text(encoding="utf-8") == "hello"

    # 回放：工具卡在块内、最终回复在块外
    from app.sessions import replay

    result = replay.load_replay(deps.recorder.db, "s1")
    turn = result["turns"][0]
    assert turn["final_text"] == "写好了"
    assert any(x["kind"] == "tool_call" and x["status"] == "completed" for x in turn["work_items"])
    assert any(x["kind"] == "text" and x["text"] == "我写个文件" for x in turn["work_items"])


def test_tool_error_maps_to_failed(recorder) -> None:
    """工具执行失败（Error 输出）：状态 failed，错误照样喂回模型继续。"""
    client = FakeClient([
        [{"type": "tool_use", "id": "tc9", "name": "read_file", "input": {"path": "nope.txt"}}],
        [{"type": "text_delta", "text": "文件不存在，我换个办法"}],
    ])
    deps = TurnDeps(client=client, recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "读一下"}])
    events = _run(deps)
    done = next(e for e in events if e["type"] == "tool_completed")
    assert done["status"] == "failed"
    assert events[-1]["state"] == "success"  # 工具失败不打断循环，turn 本身成功收口


# ---------- 权限分档 ----------


def test_deny_list_hard_reject(recorder) -> None:
    """策略硬拒：无 tool_started，结果为 Error 且状态 denied，模型继续。"""
    client = FakeClient([
        [{"type": "tool_use", "id": "tc2", "name": "bash", "input": {"command": "rm -rf /"}}],
        [{"type": "text_delta", "text": "好的不删了"}],
    ])
    deps = TurnDeps(client=client, recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "删库"}])
    events = _run(deps)
    done = next(e for e in events if e["type"] == "tool_completed")
    assert done["status"] == "denied"
    assert all(e["type"] != "tool_started" for e in events)


def test_approval_rejected(recorder) -> None:
    """审批回调拒绝：状态 denied，理由含"用户拒绝"。"""
    client = FakeClient([
        [{"type": "tool_use", "id": "tc3", "name": "bash", "input": {"command": "del a.txt"}}],
        [{"type": "text_delta", "text": "明白，不删"}],
    ])

    async def reject_all(tool, tool_input, reason):
        return {"approved": False, "denial_reason": "测试拒绝"}

    deps = TurnDeps(client=client, recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "删文件"}], approver=reject_all)
    events = _run(deps)
    done = next(e for e in events if e["type"] == "tool_completed")
    assert done["status"] == "denied"
    # 审批等待不计入工时：active_ms 仍为正数（暂停段被剔除，不做精确断言）
    assert events[-1]["active_ms"] >= 0


def test_approval_approved_executes(recorder) -> None:
    """审批回调批准：命令真正执行（删除类命令→批准→文件被删）。"""
    from app.agent.tools import tool_write_file

    tool_write_file("a.txt", "x")
    client = FakeClient([
        [{"type": "tool_use", "id": "tc4", "name": "bash", "input": {"command": "Remove-Item a.txt"}}],
        [{"type": "text_delta", "text": "执行完成"}],
    ])
    approved_calls: list[str] = []

    async def approve_all(tool, tool_input, reason):
        approved_calls.append(tool)
        return {"approved": True, "denial_reason": None}

    deps = TurnDeps(client=client, recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "删掉 a.txt"}], approver=approve_all)
    events = _run(deps)
    done = next(e for e in events if e["type"] == "tool_completed")
    assert done["status"] == "completed"
    assert approved_calls == ["bash"]
    # 审批通过后命令真实执行：文件已被删除
    assert not (workspace_root() / "a.txt").exists()


# ---------- 停止与异常 ----------


def test_stop_mid_stream(recorder) -> None:
    """流中停止：已生成部分保留，状态 stopped。"""
    client = FakeClient([
        [
            {"type": "text_delta", "text": "部分输出1"},
            {"type": "text_delta", "text": "部分输出2"},
            {"type": "text_delta", "text": "不应出现"},
        ]
    ])
    state_box = {"count": 0}

    def stop_after_two() -> bool:
        state_box["count"] += 1
        # 轮询点：循环起点(1)、每个 delta(2/3/4)。阈值 3 = 前两个 delta 放行，第三个前停止
        return state_box["count"] > 3

    deps = TurnDeps(client=client, recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "长文"}], stop_flag=stop_after_two)
    events = _run(deps)
    assert events[-1]["state"] == "stopped"
    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    assert deltas == "部分输出1部分输出2"

    from app.sessions import replay

    result = replay.load_replay(deps.recorder.db, "s1")
    assert result["turns"][0]["state"] == "stopped"
    assert result["turns"][0]["final_text"] == "部分输出1部分输出2"


def test_llm_error_fails_turn(recorder) -> None:
    """LLM 异常：error 事件 + failed 收口 + 错误卡片落盘。"""
    class BoomClient:
        async def stream(self, *, system, messages, tools):
            raise RuntimeError("模拟网络失败")
            yield  # pragma: no cover（使其成为异步生成器）

    deps = TurnDeps(client=BoomClient(), recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "hi"}])
    events = _run(deps)
    assert any(e["type"] == "error" for e in events)
    assert events[-1]["state"] == "failed"

    from app.sessions import replay

    result = replay.load_replay(deps.recorder.db, "s1")
    assert any(x["kind"] == "error" for x in result["turns"][0]["work_items"])
