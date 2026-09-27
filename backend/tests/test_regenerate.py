"""BE-9 验证：重新生成（回滚重跑 / 用户消息保留 / 事实清理 / 404/409 语义）。"""

import asyncio
import json

from app.api.sessions import get_llm_client
from app.sessions import replay, store, turn_manager


class ScriptClient:
    """两轮脚本：第一轮生成 A，重新生成后改出 B。"""

    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)

    async def stream(self, *, system, messages, tools):
        yield {"type": "text_delta", "text": self.texts.pop(0)}


def _sse_events(client, path: str, json_body: dict | None = None) -> tuple[int, list[dict]]:
    with client.stream("POST", path, json=json_body) as resp:
        status = resp.status_code
        raw = "".join(resp.iter_text())
    events = []
    for block in raw.split("\n\n"):
        data = next((l for l in block.splitlines() if l.startswith("data:")), None)
        if data:
            events.append(json.loads(data[len("data: "):]))
    return status, events


def test_regenerate_full_flow(client) -> None:
    """重新生成：旧回复被回滚、用户消息保留、新回复落盘、旧 turn 事实清除。"""
    sid = client.post("/api/sessions").json()["id"]
    client.app.dependency_overrides[get_llm_client] = lambda: ScriptClient(["第一版回答"])

    _, first = _sse_events(client, f"/api/sessions/{sid}/turn", {"text": "打个招呼"})
    assert first[-1]["state"] == "success"
    detail1 = client.get(f"/api/sessions/{sid}").json()
    assert len(detail1["turns"]) == 1
    assert detail1["turns"][0]["final_text"] == "第一版回答"
    old_turn_id = detail1["turns"][0]["turn_id"]

    # 重新生成：改出第二版
    client.app.dependency_overrides[get_llm_client] = lambda: ScriptClient(["第二版更好的回答"])
    status, second = _sse_events(client, f"/api/sessions/{sid}/regenerate")
    assert status == 200
    assert second[-1]["state"] == "success"

    detail2 = client.get(f"/api/sessions/{sid}").json()
    assert len(detail2["turns"]) == 1  # 旧轮消失，只剩新轮
    new_turn = detail2["turns"][0]
    assert new_turn["turn_id"] != old_turn_id
    assert new_turn["final_text"] == "第二版更好的回答"
    assert new_turn["user_message"]["text"] == "打个招呼"  # 用户消息保留

    # 模型历史正确：user → assistant(第二版)，且没有旧回复残留
    from app import db

    with db.new_session() as d:
        history = replay.load_history(d, sid)
    assert history[0] == {"role": "user", "content": "打个招呼"}
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == [{"type": "text", "text": "第二版更好的回答"}]
    assert len(history) == 2

    # 旧 turn 事实已清除（turn 事实只剩新轮的）
    with db.new_session() as d:
        facts = store.list_entries(d, sid, "turn")
    assert [store.entry_data(f)["turn_id"] for f in facts] == [new_turn["turn_id"]]


def test_regenerate_requires_existing_turn(client) -> None:
    """没有用户消息的会话（draft）→ 404。"""
    sid = client.post("/api/sessions").json()["id"]
    resp = client.post(f"/api/sessions/{sid}/regenerate")
    assert resp.status_code == 404


def test_regenerate_rejects_when_running(client) -> None:
    """进行中的会话不可重新生成 → 409。"""
    sid = client.post("/api/sessions").json()["id"]
    turn_manager.register(sid, "t0")
    try:
        resp = client.post(f"/api/sessions/{sid}/regenerate")
        assert resp.status_code == 409
    finally:
        turn_manager.unregister(sid, "t0")
