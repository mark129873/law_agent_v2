"""启动资源责任移交回归：注入失败，随后用同会话真实API成功重试。"""

import pytest

from app import db
from app.api import sessions
from app.sessions import turn_manager


@pytest.mark.parametrize("endpoint", ["turn", "regenerate"])
@pytest.mark.parametrize("failure", ["history", "prompt", "deps", "response", "schedule"])
def test_setup_failure_releases_turn_and_db(client, monkeypatch, endpoint, failure):
    """注册后的每个装配阶段失败都关闭Session、注销运行态，不遗留后台模型任务。"""
    class FakeClient:
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            yield {"type": "text_delta", "text": "成功"}

    fake = FakeClient()
    client.app.dependency_overrides[sessions.get_llm_client] = lambda: fake
    sid = client.post("/api/sessions").json()["id"]
    if endpoint == "regenerate":
        assert client.post(f"/api/sessions/{sid}/turn", json={"text": "测试"}).status_code == 200
    initial_calls = fake.calls
    opened = []
    original_new = db.new_session

    def tracked_new():
        session = original_new()
        original_close = session.close
        session.was_closed = False
        def close():
            session.was_closed = True
            original_close()
        session.close = close
        opened.append(session)
        return session

    monkeypatch.setattr(db, "new_session", tracked_new)
    def fail(*args, **kwargs):
        raise RuntimeError("setup-test-failure")

    targets = {
        "history": (sessions.replay, "load_history"),
        "prompt": (sessions, "_load_system_prompt"),
        "deps": (sessions, "TurnDeps"),
        "response": (sessions, "StreamingResponse"),
        "schedule": (sessions.asyncio, "create_task"),
    }
    with monkeypatch.context() as injection:
        target, name = targets[failure]
        injection.setattr(target, name, fail)
        with pytest.raises(RuntimeError, match="setup-test-failure"):
            client.post(f"/api/sessions/{sid}/{endpoint}", json={"text": "测试"})
    assert opened and all(session.was_closed for session in opened)
    assert not turn_manager.is_running(sid)
    assert sid not in turn_manager._stop_events
    assert fake.calls == initial_calls
    response = client.post(f"/api/sessions/{sid}/{endpoint}", json={"text": "重试"})
    assert response.status_code == 200
    assert '"state": "success"' in response.text
    assert fake.calls == initial_calls + 1
    assert all(session.was_closed for session in opened)
    assert not turn_manager.is_running(sid)
