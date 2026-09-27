"""BE-3 验证：会话 CRUD API（draft 新建 / 列表 / 详情回放 / 删除 / 404/409 语义）。"""

from app.sessions import store


def test_create_session_is_draft(client) -> None:
    """POST 只发 id 不落库：列表里看不到，详情 404。"""
    resp = client.post("/api/sessions")
    assert resp.status_code == 200
    sid = resp.json()["id"]
    assert len(sid) == 32

    assert client.get("/api/sessions").json() == []
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_first_message_persists_session(client) -> None:
    """首条消息（ensure_session）之后：列表可见、详情可回放（resume 读取路径）。"""
    sid = client.post("/api/sessions").json()["id"]
    # 模拟 turn 落盘的第一步（真实 turn 流程在 BE-5 接入）
    from app import db

    with db.new_session() as d:
        store.ensure_session(d, sid, model="test-model", first_user_text="帮我写脚本")
        store.upsert_message(d, sid, "m1", "user", {"text": "帮我写脚本"}, "t1")
        store.upsert_message(d, sid, "a1", "assistant", {"text": ""}, "t1")
        store.upsert_part(d, sid, "a1", "p1", "text", {"text": "好的"}, "t1")

    items = client.get("/api/sessions").json()
    assert len(items) == 1 and items[0]["id"] == sid
    assert items[0]["title"].startswith("帮我写脚本")
    assert items[0]["running"] is False

    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["session"]["id"] == sid
    assert detail["turns"][0]["user_message"]["text"] == "帮我写脚本"
    assert detail["turns"][0]["final_text"] == "好的"


def test_delete_session(client) -> None:
    """删除：软删后列表不可见、详情 404、重复删 404。"""
    sid = client.post("/api/sessions").json()["id"]
    from app import db

    with db.new_session() as d:
        store.ensure_session(d, sid, "test-model", "临时会话")

    assert client.delete(f"/api/sessions/{sid}").json() == {"ok": True}
    assert client.get("/api/sessions").json() == []
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.delete(f"/api/sessions/{sid}").status_code == 404
