"""BE-5 验证：turn SSE 端点（假 LLM 注入 / 事件序列 / 409 / 400 / 停止流程）。"""

import json

from app.api.sessions import get_llm_client
from app.sessions import turn_manager


class ScriptClient:
    """两轮脚本客户端：第一轮吐文本，第二轮收口（验证最简 SSE 流）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        if self.calls == 1:
            yield {"type": "text_delta", "text": "你好呀"}
            yield {"type": "usage", "input_tokens": 10, "output_tokens": 4}
        else:
            yield {"type": "text_delta", "text": "（续）"}


def _parse_sse(raw: str) -> list[dict]:
    """解析 SSE 文本帧为事件列表（event: X / data: {...} 成对）。"""
    events: list[dict] = []
    for block in raw.split("\n\n"):
        lines = [l for l in block.splitlines() if l]
        if not lines or lines[0].startswith(":"):
            continue  # 心跳注释帧
        data_line = next((l for l in lines if l.startswith("data:")), None)
        if data_line:
            events.append(json.loads(data_line[len("data: "):]))
    return events


def _stream_turn(client, sid: str, text: str) -> list[dict]:
    with client.stream("POST", f"/api/sessions/{sid}/turn", json={"text": text}) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return _parse_sse(raw)


def test_turn_sse_success(client) -> None:
    """SSE 事件序列完整，落盘可回放（final_text 与 token 累计）。"""
    sid = client.post("/api/sessions").json()["id"]
    client.app.dependency_overrides[get_llm_client] = lambda: ScriptClient()

    events = _stream_turn(client, sid, "打个招呼")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "turn_started"
    assert kinds[-1] == "turn_completed"
    assert events[-1]["state"] == "success"
    assert {"type": "delta", "text": "你好呀"} in events
    assert any(e["type"] == "token_count" and e["output_tokens"] == 4 for e in events)

    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["turns"][0]["final_text"] == "你好呀"
    assert detail["session"]["tokens_used"] == 4
    # 收口后 running 复位
    assert client.get("/api/sessions").json()[0]["running"] is False


def test_turn_rejects_when_running(client) -> None:
    """同会话并发 turn → 409（跨会话不受影响）。"""
    sid = client.post("/api/sessions").json()["id"]
    turn_manager.register(sid, "t0")
    try:
        resp = client.post(f"/api/sessions/{sid}/turn", json={"text": "hi"})
        assert resp.status_code == 409
    finally:
        turn_manager.unregister(sid, "t0")


def test_turn_requires_llm_config(client, monkeypatch) -> None:
    """缺少 LLM 配置 → 400（不启动循环）。"""
    from app.config import settings

    sid = client.post("/api/sessions").json()["id"]
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    resp = client.post(f"/api/sessions/{sid}/turn", json={"text": "hi"})
    assert resp.status_code == 400


def test_stop_endpoint(client) -> None:
    """stop：无进行中 turn → 404；有 → ok 且事件置位。"""
    sid = client.post("/api/sessions").json()["id"]
    assert client.post(f"/api/sessions/{sid}/stop").status_code == 404

    event = turn_manager.register(sid, "t1")
    assert client.post(f"/api/sessions/{sid}/stop").json() == {"ok": True}
    assert event.is_set() is True
    turn_manager.unregister(sid, "t1")


def test_turn_self_stop_keeps_partial(client) -> None:
    """流中停止：turn 以 stopped 收口，已生成部分保留（部分输出语义）。"""
    sid = client.post("/api/sessions").json()["id"]

    class SelfStopClient:
        async def stream(self, *, system, messages, tools):
            yield {"type": "text_delta", "text": "部分"}
            turn_manager.request_stop(sid)  # 模拟用户此刻点了停止
            yield {"type": "text_delta", "text": "不应出现"}

    client.app.dependency_overrides[get_llm_client] = lambda: SelfStopClient()
    events = _stream_turn(client, sid, "写长文")
    assert events[-1]["state"] == "stopped"
    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    assert deltas == "部分"

    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["turns"][0]["state"] == "stopped"
    assert detail["turns"][0]["final_text"] == "部分"
