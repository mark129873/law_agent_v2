"""删除后的旧页面不能继续执行，也不能借失败请求修改模式或回滚历史。"""

import pytest
from sqlalchemy import select

from app import db
from app.api.sessions import get_llm_client
from app.models import Base
from app.sessions import turn_manager


def _snapshot():
    """比较六表完整内容，确保拒绝请求没有偷偷追加消息、扣用量或修改配置。"""
    with db.new_session() as session:
        return {
            table.name: sorted(map(tuple, session.execute(select(table)).all()), key=repr)
            for table in Base.metadata.sorted_tables
        }


@pytest.mark.parametrize("endpoint", ["turn", "regenerate"])
def test_archived_session_rejects_execution_without_side_effects(client, endpoint):
    class FakeClient:
        calls = 0

        async def stream(self, **kwargs):
            self.calls += 1
            yield {"type": "text_delta", "text": "保留原回复"}
            yield {"type": "usage", "input_tokens": 5, "output_tokens": 3}

    fake = FakeClient()
    client.app.dependency_overrides[get_llm_client] = lambda: fake
    sid = client.post("/api/sessions").json()["id"]
    response = client.post(f"/api/sessions/{sid}/turn", json={"text": "原问题", "mode": "build"})
    assert response.status_code == 200
    assert '"state": "success"' in response.text
    assert fake.calls == 1
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    before = _snapshot()

    response = client.post(f"/api/sessions/{sid}/{endpoint}", json={"text": "旧页面重发", "mode": "yolo"})

    assert response.status_code == 404
    assert fake.calls == 1
    assert not turn_manager.is_running(sid)
    assert sid not in turn_manager._stop_events
    assert _snapshot() == before
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.get("/api/sessions").json() == []


def test_delete_rejects_registered_turn_and_allows_after_stop(client):
    """删除与执行登记使用同一事件循环，运行中的会话必须先停止。"""
    from app.sessions import store

    sid = client.post("/api/sessions").json()["id"]
    with db.new_session() as session:
        store.ensure_session(session, sid, "test-model", "删除保护")
    turn_manager.register(sid, "active-turn")
    try:
        assert client.delete(f"/api/sessions/{sid}").status_code == 409
        assert client.get(f"/api/sessions/{sid}").status_code == 200
    finally:
        turn_manager.unregister(sid, "active-turn")
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
