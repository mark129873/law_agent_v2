"""BE-6 验证：交互审批（仲裁注册表 / InteractiveApprover 全流程 / 端点 / 工时记账）。"""

import asyncio

from app.sessions import approvals, replay, store
from app.sessions.approvals import InteractiveApprover
from app.sessions.recorder import TurnRecorder


def _recorder(store_db) -> TurnRecorder:
    return TurnRecorder(store_db, "s1", model="test-model", max_tokens=1000)


def test_approval_registry_resolve_once(store_db) -> None:
    """注册表：resolve 唤醒等待方；二次 resolve 返回 False（已决不可再改）。"""
    async def scenario():
        slot = approvals.register("r1")
        assert approvals.resolve("r1", True) is True
        await slot["event"].wait()
        assert slot["approved"] is True
        assert approvals.resolve("r1", False) is False  # 已移除
        assert approvals.resolve("unknown", True) is False

    asyncio.run(scenario())


def test_interactive_approver_full_flow(store_db) -> None:
    """全流程：请求入队 → 挂起 → 拒绝 → 唤醒 → 两条留痕 → resolved 事件。"""
    async def scenario():
        store.ensure_session(store_db, "s1", "test-model", "删文件")  # 外键依赖：会话行必须先存在
        recorder = _recorder(store_db)
        recorder.turn_id = "t1"  # 测试固定 turn 标签
        queue: asyncio.Queue = asyncio.Queue()
        approver = InteractiveApprover(store_db, "s1", recorder, queue)

        wait_task = asyncio.create_task(approver("bash", {"command": "del a.txt"}, "删除类命令"))

        request_event = await asyncio.wait_for(queue.get(), timeout=5)
        assert request_event["type"] == "approval_request"
        assert request_event["tool"] == "bash"
        request_id = request_event["request_id"]

        approvals.resolve(request_id, False)
        approved = await asyncio.wait_for(wait_task, timeout=5)

        resolved_event = await asyncio.wait_for(queue.get(), timeout=5)
        assert resolved_event == {"type": "approval_resolved", "request_id": request_id, "approved": False}
        assert approved is False

        # 两条事实留痕：requested + denied
        entries = store.list_entries(store_db, "s1", "approval")
        statuses = [store.entry_data(e)["status"] for e in entries]
        assert statuses == ["requested", "denied"]

    asyncio.run(scenario())


def test_interactive_approver_approved(store_db) -> None:
    """批准路径：返回 True，留痕为 approved。"""
    async def scenario():
        store.ensure_session(store_db, "s1", "test-model", "删文件")
        recorder = _recorder(store_db)
        recorder.turn_id = "t1"
        queue: asyncio.Queue = asyncio.Queue()
        approver = InteractiveApprover(store_db, "s1", recorder, queue)

        wait_task = asyncio.create_task(approver("bash", {"command": "Remove-Item tmp.txt"}, "删除类命令"))
        request_event = await asyncio.wait_for(queue.get(), timeout=5)
        approvals.resolve(request_event["request_id"], True)
        assert await asyncio.wait_for(wait_task, timeout=5) is True

        entries = store.list_entries(store_db, "s1", "approval")
        assert store.entry_data(entries[-1])["status"] == "approved"

    asyncio.run(scenario())


def test_approval_endpoint(client, store_db) -> None:
    """端点：未知 request_id → 404；已注册的 → ok 且事件置位。"""
    sid = client.post("/api/sessions").json()["id"]
    assert client.post(
        f"/api/sessions/{sid}/approval", json={"request_id": "nope", "approved": True}
    ).status_code == 404

    async def register_slot():
        return approvals.register("r9")

    slot = asyncio.run(register_slot())
    resp = client.post(f"/api/sessions/{sid}/approval", json={"request_id": "r9", "approved": True})
    assert resp.json() == {"ok": True}
    assert slot["approved"] is True and slot["event"].is_set()


def test_active_ms_excludes_approval_wait(store_db, tmp_data_dir) -> None:
    """工时记账：审批等待段不计入 active_ms（pause/resume 由循环调用）。"""
    import time

    from app import db

    db.init_db(tmp_data_dir)
    recorder = _recorder(store_db)
    recorder.begin_turn("测工时")

    recorder.pause_active()
    time.sleep(0.12)  # 模拟用户思考要不要批准
    recorder.resume_active()
    fact = recorder.end_turn("success")

    assert fact["active_ms"] < 120  # 等待段被剔除（否则 active_ms ≈ 120+）
    assert fact["state"] == "success"


def test_pending_approval_survives_in_replay(store_db) -> None:
    """刷新恢复：未决审批在回放中出现，前端可凭 request_id 提交决定。"""
    store.ensure_session(store_db, "s1", "test-model", "删文件")
    store.put_entry(store_db, "s1", "approval", {
        "turn_id": "t1", "request_id": "r-refresh", "tool": "bash",
        "input": {"command": "del a.txt"}, "reason": "删除类命令",
        "status": "requested", "time": 1.0,
    })
    result = replay.load_replay(store_db, "s1")
    assert result["pending_approval"]["request_id"] == "r-refresh"
