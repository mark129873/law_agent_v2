"""用量聚合验证（docs/ARCHITECTURE.md §3.7）：
turn 事实三级 token 字段 + 会话双列累计 + compact 真实预算激活 + regenerate 重算修正。
"""

import asyncio

import pytest

from app.agent.loop import run_turn
from app.models import Session
from app.sessions import replay, store


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


class FakeClient:
    """一步假流式客户端：回答 + usage(9/3，含缓存计量)，无工具。"""

    model = "test-model"

    async def stream(self, *, system, messages, tools):
        yield {"type": "text_delta", "text": "回答"}
        yield {"type": "usage", "input_tokens": 9, "output_tokens": 3,
               "cache_read_tokens": 7, "cache_creation_tokens": 2}


def test_turn_usage_fact_and_session_columns(recorder) -> None:
    """跑一轮：turn 事实带输入/上下文占用，会话双列累加，compact 预算喂到真实 input。"""
    from app.agent.loop import TurnDeps

    deps = TurnDeps(
        client=FakeClient(), recorder=recorder, system_prompt="测试",
        history=[{"role": "user", "content": "你好"}],
    )
    events = asyncio.run(_collect(deps))

    assert events[-1]["state"] == "success"
    # compact 预算拿到真实 input（激活此前无人赋值的死路径）
    assert deps.last_input_tokens == 9
    # turn 事实：输出累计 / 输入累计 / 最近一步 input（上下文占用口径）/ 缓存读写
    fact = store.entry_data(store.list_entries(recorder.db, "s1", "turn")[0])
    assert fact["tokens_used"] == 3
    assert fact["input_tokens"] == 9
    assert fact["context_tokens"] == 9
    assert fact["cache_read_tokens"] == 7
    assert fact["cache_creation_tokens"] == 2
    # 会话双列
    row = recorder.db.get(Session, "s1")
    assert row.tokens_used == 3
    assert row.input_tokens == 9


async def _collect(deps) -> list[dict]:
    return [e async for e in run_turn(deps)]


def test_recalc_session_usage_after_rollback(recorder) -> None:
    """regenerate 回滚后重算：被删轮的 token 不再计入（旧实现漏接线导致双算）。"""
    store.ensure_session(recorder.db, "s1", model="test-model", first_user_text="t")
    # 两轮事实：turn1(3/9)、turn2(4/20)；会话列是累加后的 7/29
    store.put_entry(recorder.db, "s1", "turn",
                    {"turn_id": "t1", "tokens_used": 3, "input_tokens": 9, "context_tokens": 9},
                    turn_id="t1")
    store.put_entry(recorder.db, "s1", "turn",
                    {"turn_id": "t2", "tokens_used": 4, "input_tokens": 20, "context_tokens": 20},
                    turn_id="t2")
    row = recorder.db.get(Session, "s1")
    row.tokens_used = 7
    row.input_tokens = 29
    recorder.db.commit()

    # 模拟 regenerate：回滚 turn2 的全部事实，然后重算
    store.rollback_turn(recorder.db, "s1", "t2", from_sequence=10 ** 9)
    replay.recalc_session_usage(recorder.db, "s1")

    row = recorder.db.get(Session, "s1")
    assert row.tokens_used == 3  # 只剩 turn1 的输出
    assert row.input_tokens == 9  # 只剩 turn1 的输入
