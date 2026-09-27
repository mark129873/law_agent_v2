"""BE-2 验证：会话存储层（四表 upsert / turn 事实 / 软删 / 回放拼装 / 历史重建）。

全部使用 tests/.tmp-data/ 沙箱与真实 SQLite 引擎，不涉及 LLM 调用。
"""

import json

import pytest
from sqlalchemy import select

from app import db
from app.models import Part
from app.sessions import replay, store


@pytest.fixture()
def store_db(tmp_data_dir):
    """一个连到测试沙箱库的 ORM 会话；测试结束自动关闭。"""
    db.init_db(tmp_data_dir)
    s = db.new_session()
    yield s
    s.close()


def _session(d, sid="s1"):
    return store.ensure_session(d, sid, model="test-model", first_user_text="帮我写个脚本")


# ---------- 基础写入 ----------


def test_draft_ensure_session_once(store_db) -> None:
    """draft 语义：ensure 幂等；标题取首条用户消息截断 30 字。"""
    d = store_db
    long_text = "这行标题特别长特别长特别长特别长特别长特别长特别长特别长特别长特别长应该被截断"
    row = store.ensure_session(d, "s1", "test-model", long_text)
    again = store.ensure_session(d, "s1", "other-model", "第二次不该生效")
    assert row.id == again.id and row.title == again.title
    assert len(row.title) <= 30
    assert row.model == "test-model"


def test_message_sequence_assign_and_preserve(store_db) -> None:
    """sequence：首次 max+1 递增；同 id 再次 upsert（正文更新）时保留原 sequence。"""
    d = store_db
    _session(d)
    m1 = store.upsert_message(d, "s1", "m1", "user", {"text": "你好"}, "t1")
    m2 = store.upsert_message(d, "s1", "m2", "assistant", {"text": ""}, "t1")
    assert (m1.sequence, m2.sequence) == (1, 2)

    # 同 id 重写：正文更新、sequence 不变（ZCode 防时间线漂移规则）
    m1_again = store.upsert_message(d, "s1", "m1", "user", {"text": "你好（修正）"}, "t1")
    assert m1_again.sequence == 1
    assert json.loads(m1_again.data)["text"] == "你好（修正）"
    assert m1_again.time_updated >= m1_again.time_created


def test_part_lifecycle_upsert(store_db) -> None:
    """工具部件：同一 partID 按生命周期逐态推进，行数始终为 1。"""
    d = store_db
    _session(d)
    store.upsert_message(d, "s1", "a1", "assistant", {"text": ""}, "t1")
    common = dict(session_id="s1", message_id="a1", kind="tool_call", turn_id="t1")
    store.upsert_part(d, part_id="p1", data={"name": "bash", "status": "pending"}, **common)
    store.upsert_part(d, part_id="p1", data={"name": "bash", "status": "running"}, **common)
    final = store.upsert_part(
        d, part_id="p1", data={"name": "bash", "status": "completed", "output": "ok"}, **common
    )
    rows = list(d.execute(select(Part)).scalars())
    assert len(rows) == 1
    assert json.loads(final.data)["status"] == "completed"


def test_soft_delete(store_db) -> None:
    """软删：get/list 不可见，重复删返回 False。"""
    d = store_db
    _session(d)
    store.upsert_message(d, "s1", "m1", "user", {"text": "hi"}, "t1")
    assert store.list_sessions(d) != []
    assert store.soft_delete_session(d, "s1") is True
    assert store.get_session(d, "s1") is None
    assert store.list_sessions(d) == []
    assert store.soft_delete_session(d, "s1") is False


# ---------- 回放拼装 ----------


def _seed_two_turns(d) -> None:
    """构造两轮对话：t1 完整收口；t2 只有部分行（孤儿轮）。"""
    _session(d)
    # turn1：用户 → assistant(text 过程 + tool_call + text 最终) + turn 事实
    store.upsert_message(d, "s1", "u1", "user", {"text": "查一下文件"}, "t1")
    store.upsert_message(d, "s1", "a1", "assistant", {"text": ""}, "t1")
    store.upsert_part(d, "s1", "a1", "p_t1_text1", "text", {"text": "我先看看目录"}, "t1")
    store.upsert_part(
        d, "s1", "a1", "p_t1_tool", "tool_call",
        {"tool_call_id": "tc1", "name": "glob", "input": {"pattern": "*"}, "status": "completed", "output": "a.py"},
        "t1",
    )
    store.upsert_part(d, "s1", "a1", "p_t1_text2", "text", {"text": "目录里有 a.py，结论如下"}, "t1")
    store.put_entry(d, "s1", "turn", {
        "turn_id": "t1", "started_at": 1.0, "ended_at": 2000.0,
        "active_ms": 1800.0, "state": "success", "tokens_used": 100,
    })
    # turn2：孤儿轮（无收口事实）
    store.upsert_message(d, "s1", "u2", "user", {"text": "继续"}, "t2")
    store.upsert_message(d, "s1", "a2", "assistant", {"text": ""}, "t2")
    store.upsert_part(d, "s1", "a2", "p_t2_text", "text", {"text": "部分输出"}, "t2")


def test_replay_groups_and_final_text(store_db) -> None:
    """回放：按 turn 分组、末条 text 提升为 final_text、工具留在块内、状态取事实。"""
    d = store_db
    _seed_two_turns(d)
    result = replay.load_replay(d, "s1", running_turn_ids=set())
    assert result is not None
    t1, t2 = result["turns"]
    # t1：成功收口，耗时取落盘事实
    assert t1["state"] == "success" and t1["active_ms"] == 1800.0
    assert t1["user_message"]["text"] == "查一下文件"
    assert t1["final_text"] == "目录里有 a.py，结论如下"
    kinds = [x["kind"] for x in t1["work_items"]]
    assert "text" in kinds and "tool_call" in kinds
    # 末条 text 已从块内剔除
    assert all(not (x["kind"] == "text" and x["text"] == t1["final_text"]) for x in t1["work_items"])
    # t2：无收口事实且不在 running 集合 → 孤儿轮标记 stopped
    assert t2["state"] == "stopped"
    assert t2["final_text"] == "部分输出"


def test_replay_running_turn(store_db) -> None:
    """运行中的轮次：无事实但在 running 集合里 → state=running。"""
    d = store_db
    _seed_two_turns(d)
    result = replay.load_replay(d, "s1", running_turn_ids={"t2"})
    assert result["turns"][1]["state"] == "running"


def test_replay_approval_trace_and_pending(store_db) -> None:
    """审批：留痕进工作块；未决审批出现在 pending_approval；处理后消失。"""
    d = store_db
    _session(d)
    store.upsert_message(d, "s1", "u1", "user", {"text": "删除文件"}, "t1")
    store.put_entry(d, "s1", "approval", {
        "turn_id": "t1", "request_id": "r1", "tool": "bash",
        "input": {"command": "del a.txt"}, "reason": "shell 删除命令",
        "status": "requested", "time": 1.0,
    })
    result = replay.load_replay(d, "s1")
    assert result["pending_approval"]["request_id"] == "r1"
    assert any(x["kind"] == "approval" for x in result["turns"][0]["work_items"])

    # 用户批准：再写一条同 request_id 的已决事实（留痕），未决消失
    store.put_entry(d, "s1", "approval", {
        "turn_id": "t1", "request_id": "r1", "tool": "bash",
        "input": {"command": "del a.txt"}, "reason": "shell 删除命令",
        "status": "approved", "time": 2.0,
    })
    result2 = replay.load_replay(d, "s1")
    assert result2["pending_approval"] is None
    statuses = [x["status"] for x in result2["turns"][0]["work_items"] if x["kind"] == "approval"]
    assert statuses == ["requested", "approved"]  # 两次都留痕


def test_replay_deleted_session_invisible(store_db) -> None:
    """已软删会话回放返回 None。"""
    d = store_db
    _seed_two_turns(d)
    store.soft_delete_session(d, "s1")
    assert replay.load_replay(d, "s1") is None


# ---------- 模型历史重建 ----------


def test_load_history_tool_roundtrip(store_db) -> None:
    """历史重建：assistant 的 text+tool_use 与合成 tool_result、后续用户消息齐全。"""
    d = store_db
    _session(d)
    store.upsert_message(d, "s1", "u1", "user", {"text": "看看目录"}, "t1")
    store.upsert_message(d, "s1", "a1", "assistant", {"text": ""}, "t1")
    store.upsert_part(d, "s1", "a1", "pt", "text", {"text": "我看看"}, "t1")
    store.upsert_part(
        d, "s1", "a1", "pc", "tool_call",
        {"tool_call_id": "tc1", "name": "glob", "input": {"pattern": "*"}, "status": "completed", "output": "a.py"},
        "t1",
    )
    store.upsert_message(d, "s1", "u2", "user", {"text": "谢谢"}, "t2")

    history = replay.load_history(d, "s1")
    assert history[0] == {"role": "user", "content": "看看目录"}
    assistant = history[1]
    assert assistant["role"] == "assistant"
    assert {"type": "text", "text": "我看看"} in assistant["content"]
    tool_use = next(b for b in assistant["content"] if b["type"] == "tool_use")
    assert tool_use["id"] == "tc1"
    # 合成的 tool_result 紧跟其后
    tool_result_msg = history[2]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"][0]["type"] == "tool_result"
    assert tool_result_msg["content"][0]["tool_use_id"] == "tc1"
    assert history[3] == {"role": "user", "content": "谢谢"}


def test_load_history_compact_boundary(store_db) -> None:
    """压缩边界：before_sequence 之前的历史不再发给模型，摘要以 user 消息置顶。"""
    d = store_db
    _session(d)
    store.upsert_message(d, "s1", "u1", "user", {"text": "第一轮"}, "t1")
    store.upsert_message(d, "s1", "a1", "assistant", {"text": ""}, "t1")
    store.upsert_part(d, "s1", "a1", "p1", "text", {"text": "第一轮回复"}, "t1")
    store.put_entry(d, "s1", "compaction", {
        "turn_id": "t2", "before_sequence": 2, "summary_text": "之前聊了第一轮",
        "tokens_before": 500, "tokens_after": 120, "time": 1.0,
    })
    store.upsert_message(d, "s1", "u2", "user", {"text": "第二轮"}, "t2")

    history = replay.load_history(d, "s1")
    assert history[0]["role"] == "user"
    assert "之前聊了第一轮" in history[0]["content"]
    flat = json.dumps(history, ensure_ascii=False)
    assert "第一轮回复" not in flat  # 边界前的正文不再下发
    assert {"role": "user", "content": "第二轮"} in history
