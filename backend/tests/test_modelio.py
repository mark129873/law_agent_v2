"""BE-012 验证：model-io JSONL 逐调用记录（替代原 Langfuse 观测）。

纪律（docs/RELIABILITY.md）：MODELIO_DIR 由 conftest 指向测试沙箱，
真实 log/ 绝不被测试触碰；一律假 LLM。
"""

import json
from pathlib import Path

import pytest

from app.config import settings


@pytest.fixture()
def recorder(tmp_data_dir):
    """连到测试沙箱库的 recorder（与 test_agent_loop 同款）。"""
    from app.agent.tools import workspace_root
    from app import db
    from app.sessions.recorder import TurnRecorder

    workspace_root().mkdir(parents=True, exist_ok=True)
    db.init_db(tmp_data_dir)
    session = db.new_session()
    rec = TurnRecorder(session, "s1", model="test-model", max_tokens=1000)
    yield rec
    session.close()


def _read_model_io(session_id: str = "s1") -> list[dict]:
    """读回某会话的 model-io JSONL 并解析为列表。"""
    path = Path(settings.modelio_dir) / f"model-io-{session_id}.jsonl"
    assert path.exists(), f"model-io 文件不存在：{path}"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FakeClient:
    """单步假流式客户端：回答 + usage，然后收口。"""

    model = "test-model"

    async def stream(self, *, system, messages, tools):
        yield {"type": "text_delta", "text": "回答"}
        yield {"type": "usage", "input_tokens": 9, "output_tokens": 3}


def test_model_io_record_payload(recorder) -> None:
    """一次成功调用：文件逐行 JSON，字段含完整请求快照/响应/用量/耗时。"""
    from app.agent.loop import TurnDeps, run_turn

    async def run():
        return [e async for e in run_turn(TurnDeps(
            client=FakeClient(), recorder=recorder, system_prompt="测试系统提示",
            history=[{"role": "user", "content": "你好"}],
        ))]

    import asyncio
    events = asyncio.run(run())
    assert events[-1]["state"] == "success"

    lines = _read_model_io()
    assert len(lines) == 1
    rec = lines[0]
    assert rec["session_id"] == "s1"
    assert rec["turn_id"] == recorder.turn_id
    assert rec["subtask_id"] is None
    assert rec["model"] == "test-model"
    assert rec["error"] is None
    assert isinstance(rec["duration_ms"], (int, float)) and rec["duration_ms"] >= 0
    # 请求快照：system 全文 + messages 历史 + 工具名
    assert rec["request"]["system"] == "测试系统提示"
    assert rec["request"]["messages"][0]["content"] == "你好"
    assert "bash" in rec["request"]["tool_names"] and "subtask" in rec["request"]["tool_names"]
    # 响应与用量
    assert rec["response"]["text"] == "回答"
    assert rec["response"]["tool_calls"] == []
    # 假客户端未带 cache 字段 → 记 0（端点支持 prompt caching 时才非零）
    assert rec["usage"] == {
        "input_tokens": 9, "output_tokens": 3,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
    }


def test_model_io_records_tool_calls(recorder) -> None:
    """发起工具调用的步：response.tool_calls 记录 id/name/input。"""
    from app.agent.loop import TurnDeps, run_turn

    class ToolClient:
        model = "test-model"

        async def stream(self, *, system, messages, tools):
            yield {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "echo hi"}}

    async def run():
        return [e async for e in run_turn(TurnDeps(
            client=ToolClient(), recorder=recorder, system_prompt="测试",
            history=[{"role": "user", "content": "跑命令"}],
        ))]

    import asyncio
    asyncio.run(run())
    rec = _read_model_io()[0]
    assert rec["response"]["tool_calls"] == [
        {"id": "t1", "name": "bash", "input": {"command": "echo hi"}}
    ]
    assert rec["response"]["text"] == ""


def test_model_io_records_error(recorder) -> None:
    """调用失败：error 字段记录原因，turn 以 failed 收口（观测不影响主流程语义）。"""
    from app.agent.loop import TurnDeps, run_turn

    class BrokenClient:
        model = "test-model"

        async def stream(self, *, system, messages, tools):
            raise RuntimeError("网络断了")
            yield  # pragma: no cover —— 使其成为异步生成器

    async def run():
        return [e async for e in run_turn(TurnDeps(
            client=BrokenClient(), recorder=recorder, system_prompt="测试",
            history=[{"role": "user", "content": "你好"}],
        ))]

    import asyncio
    events = asyncio.run(run())
    assert events[-1]["state"] == "failed"
    rec = _read_model_io()[0]
    assert rec["error"] and "网络断了" in rec["error"]


def test_model_io_rotation(monkeypatch, tmp_data_dir) -> None:
    """大小轮转：文件超限时改名归档、新调用写新文件，记录零丢失。"""
    from app.modelio import record_llm_call

    monkeypatch.setattr(settings, "modelio_max_bytes", 1)  # 阈值压到 1 字节：每写必轮转
    for i in range(3):
        record_llm_call(
            session_id="srot", turn_id="t", model="m", system="s", messages=[],
            response_text=f"x{i}", tool_calls=[],
            usage={"input_tokens": 1, "output_tokens": 1}, duration_ms=1,
        )
    files = sorted(Path(settings.modelio_dir).glob("model-io-srot*.jsonl"))
    assert len(files) >= 2  # 至少发生一次轮转（当前文件 + 归档文件）
    total = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in files)
    assert total == 3  # 三条记录一条不丢


def test_model_io_write_failure_does_not_fail_turn(recorder, tmp_data_dir, monkeypatch) -> None:
    """只在测试沙箱构造不可写目录；调用快照失败仍能完成对话和回放。"""
    import asyncio

    from app.agent.loop import TurnDeps, run_turn
    from app.sessions import replay

    blocked = tmp_data_dir / "blocked-modelio"
    blocked.write_text("这里是文件", encoding="utf-8")
    monkeypatch.setattr(settings, "modelio_dir", blocked)

    async def run():
        return [e async for e in run_turn(TurnDeps(
            client=FakeClient(), recorder=recorder, system_prompt="测试",
            history=[{"role": "user", "content": "你好"}],
        ))]

    events = asyncio.run(run())
    assert events[-1]["state"] == "success"
    assert replay.load_replay(recorder.db, "s1")["turns"][0]["final_text"] == "回答"
