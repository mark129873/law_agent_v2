from storage_seed import seed_record
"""ZCode 对齐回归：真实 SQLite 空库，不连接模型或外部服务。"""
import json

import pytest
from sqlalchemy import inspect, select

from app.models import Message, Part, Session, TurnUsage
from app.sessions import replay, store
from app.sessions.recorder import TurnRecorder


def seed(d):
    """最小会话和用户输入。"""
    store.ensure_session(d, "s", "m", "问题")
    return store.save_user_message(d, "s", "u", "问题", "t", "m")


def test_exact_minimal_columns(store_db):
    """实际数据库没有旧 role/kind/turn_id 列，列集合对应 ZCode 子集。"""
    expected = {
        "session": {"id", "project_id", "directory", "title", "time_created", "time_updated", "time_archived"},
        "message": {"id", "session_id", "sequence", "time_created", "time_updated", "data"},
        "part": {"id", "session_id", "message_id", "sequence", "time_created", "time_updated", "data"},

    }
    schema = inspect(store_db.bind)
    for table, columns in expected.items():
        assert {c["name"] for c in schema.get_columns(table)} == columns


def test_both_roles_text_only_in_parts(store_db):
    seed(store_db)
    rec = TurnRecorder(store_db, "s", "m", 100)
    rec.turn_id = "t"
    aid = rec.step_message()
    rec.write_text_part(aid, "答案")
    messages = list(store_db.scalars(select(Message).order_by(Message.sequence)))
    assert len(messages) == 2
    assert all("text" not in json.loads(m.data) for m in messages)
    assert [store.message_role(m) for m in messages] == ["user", "assistant"]
    assert json.loads(messages[1].data)["parentID"] == "u"
    parts = list(store_db.scalars(select(Part)))
    assert {json.loads(p.data)["text"] for p in parts} == {"问题", "答案"}
    assert replay.load_history(store_db, "s") == [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": [{"type": "text", "text": "答案"}]},
    ]


def test_user_write_is_atomic(store_db, monkeypatch):
    store.ensure_session(store_db, "s", "m", "问题")
    def fail(*args, **kwargs):
        raise RuntimeError("模拟正文写入失败")
    monkeypatch.setattr(store, "upsert_part", fail)
    with pytest.raises(RuntimeError):
        store.save_user_message(store_db, "s", "u", "问题", "t")
    assert store_db.get(Message, "u") is None
    assert list(store_db.scalars(select(Part))) == []


def test_user_retry_is_idempotent(store_db):
    seed(store_db)
    store.save_user_message(store_db, "s", "u", "修正问题", "t")
    assert len(list(store_db.scalars(select(Message)))) == 1
    assert len(list(store_db.scalars(select(Part)))) == 1
    assert replay.load_history(store_db, "s")[0]["content"] == "修正问题"


def test_message_rejects_body(store_db):
    seed(store_db)
    with pytest.raises(ValueError, match="text part"):
        store.upsert_message(store_db, "s", "a", "assistant", {"text": "不能重复保存"}, "t")


def test_cross_scope_upsert_reassigns_sequence(store_db):
    """ZCode 同 ID 跨 scope 更新会改绑并分配新 scope 顺序。"""
    seed(store_db)
    store.ensure_session(store_db, 'other', 'm', '另一个')
    row = store.upsert_message(store_db, 'other', 'u', 'user', {}, 't')
    assert row.session_id == 'other' and row.sequence == 0
    with pytest.raises(ValueError, match='不属于'):
        store.upsert_part(store_db, 's', 'u', 'p', 'text', {'text': 'x'})


def test_anchor_and_model_source(store_db):
    seed(store_db)
    row = store.upsert_message(store_db, 's', 'a', 'assistant', {'modelId': 'm'}, 't')
    assert json.loads(row.data)['anchor']['turnId'] == 't'
    assert 'metadata' not in json.loads(row.data)
    assert store.session_info(store_db, store_db.get(Session, 's'))['model'] == 'm'


def test_regenerate_keeps_user_part_and_removes_turn_facts(store_db):
    seed(store_db)
    store.upsert_message(store_db, "s", "a", "assistant", {}, "t")
    store.upsert_part(store_db, "s", "a", "answer", "text", {"text": "旧答案"}, "t")
    seed_record(store_db, "s", "turn", {"tokens_used": 5}, "t")
    store.rollback_turn(store_db, "s", "t", 1)
    store.retag_message(store_db, store_db.get(Message, "u"), "new")
    assert store_db.get(Part, "answer") is None
    assert store_db.get(Part, "u") is not None
    assert all(e.turn_id != "t" for e in store.list_turns(store_db, "s"))
    result = replay.load_replay(store_db, "s")
    assert len(result["turns"]) == 1
    assert result["turns"][0]["turn_id"] == "new"
    assert result["turns"][0]["user_message"]["text"] == "问题"
    assert result["session"]["tokens_used"] == 0


def test_tool_json_roundtrip_and_stable_sequence(store_db):
    seed(store_db)
    store.upsert_message(store_db, "s", "a", "assistant", {}, "t")
    values = {"name": "read_file", "tool_call_id": "tc", "input": {"path": "a.txt"}, "status": "pending"}
    first = store.upsert_part(store_db, "s", "a", "tool", "tool_call", values, "t")
    sequence = first.sequence
    final = store.upsert_part(store_db, "s", "a", "tool", "tool_call", {**values, "status": "completed", "output": "文件"}, "t")
    raw = json.loads(final.data)
    assert raw["type"] == "tool" and raw["callID"] == "tc" and raw["tool"] == "read_file"
    assert raw["state"]["output"] == "文件"
    assert final.sequence == sequence
    history = replay.load_history(store_db, "s")
    assert history[1]["content"][0]["type"] == "tool_use"
    assert history[2]["content"][0]["content"] == "文件"


def test_running_replay_uses_turn_ids(client):
    """刷新运行中的会话，不能把 session ID 当成 turn ID。"""
    from app import db
    from app.sessions import turn_manager
    with db.new_session() as d:
        seed(d)
    turn_manager.register("s", "t")
    try:
        result = client.get("/api/sessions/s").json()
        assert result["turns"][0]["state"] == "running"
    finally:
        turn_manager.unregister("s", "t")
