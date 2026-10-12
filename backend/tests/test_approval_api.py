from storage_seed import seed_record
"""BE-6 验证：交互审批（仲裁注册表 / InteractiveApprover 全流程 / 端点 / 工时记账）。"""

import asyncio

import pytest

from app.sessions import approvals, replay, store
from app.sessions.approvals import InteractiveApprover
from app.sessions.recorder import TurnRecorder


def _recorder(store_db) -> TurnRecorder:
    return TurnRecorder(store_db, "s1", model="test-model", max_tokens=1000)


def test_approval_registry_resolve_once(store_db) -> None:
    """注册表：resolve 唤醒等待方；二次 resolve 返回 False（已决不可再改）。"""
    async def scenario():
        slot = approvals.register("r1", session_id="s1")
        assert approvals.resolve("r1", "allowOnce", session_id="s1") is True
        await slot["event"].wait()
        assert slot["approved"] is True
        assert approvals.resolve("r1", "deny", session_id="s1") is False  # 已移除
        assert approvals.resolve("unknown", "allowOnce", session_id="s1") is False

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

        approvals.resolve(request_id, "deny", session_id="s1")
        outcome = await asyncio.wait_for(wait_task, timeout=5)

        resolved_event = await asyncio.wait_for(queue.get(), timeout=5)
        assert resolved_event == {"type": "approval_resolved", "request_id": request_id, "approved": False}
        assert outcome["approved"] is False
        assert "删除类命令" in (outcome["denial_reason"] or "")

        # 两条事实留痕：requested + denied
        entries = approvals.approval_events("s1")
        statuses = [e["status"] for e in entries]
        assert statuses == ["denied"]

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
        approvals.resolve(request_event["request_id"], "allowOnce", session_id="s1")
        outcome = await asyncio.wait_for(wait_task, timeout=5)
        assert outcome["approved"] is True

        entries = approvals.approval_events("s1")
        assert entries[-1]["status"] == "approved"

    asyncio.run(scenario())


def test_approval_endpoint(client, store_db) -> None:
    """端点：未知 request_id → 404；已注册的 → ok 且事件置位。"""
    sid = client.post("/api/sessions").json()["id"]
    assert client.post(
        f"/api/sessions/{sid}/approval", json={"request_id": "nope", "approved": True}
    ).status_code == 404

    async def register_slot():
        return approvals.register("r9", session_id=sid)

    slot = asyncio.run(register_slot())
    resp = client.post(f"/api/sessions/{sid}/approval", json={"request_id": "r9", "approved": True})
    assert resp.json() == {"ok": True}
    assert slot["approved"] is True and slot["event"].is_set()


def test_duration_includes_approval_wait(store_db, tmp_data_dir) -> None:
    """工时记账：总耗时包含审批等待段。"""
    import time

    from app import db

    db.init_db(tmp_data_dir)
    recorder = _recorder(store_db)
    recorder.begin_turn("测工时")

    time.sleep(0.12)  # 模拟用户思考要不要批准
    fact = recorder.end_turn("success")

    assert fact["active_ms"] >= 120  # 等待段计入总耗时
    assert fact["state"] == "success"


def test_pending_approval_survives_in_replay(store_db) -> None:
    """刷新恢复：未决审批在回放中出现，前端可凭 request_id 提交决定。"""
    store.ensure_session(store_db, "s1", "test-model", "删文件")
    seed_record(store_db, "s1", "approval", {
        "turn_id": "t1", "request_id": "r-refresh", "tool": "bash",
        "input": {"command": "del a.txt"}, "reason": "删除类命令",
        "status": "requested", "time": 1.0,
    })
    result = replay.load_replay(store_db, "s1")
    assert result["pending_approval"]["request_id"] == "r-refresh"


# ---------- 三项审批 / 最小会话范围 / 旧选项拒绝 ----------


def test_approval_request_carries_options(store_db) -> None:
    """审批请求固定三项；命令会话范围为整条精确规则。"""
    async def scenario():
        store.ensure_session(store_db, "s1", "test-model", "删文件")
        recorder = _recorder(store_db)
        recorder.turn_id = "t1"
        queue: asyncio.Queue = asyncio.Queue()
        approver = InteractiveApprover(store_db, "s1", recorder, queue)

        wait_task = asyncio.create_task(approver("bash", {"command": "del a.txt"}, "删除类命令"))
        request_event = await asyncio.wait_for(queue.get(), timeout=5)
        approvals.resolve(request_event["request_id"], "deny", session_id="s1")
        await asyncio.wait_for(wait_task, timeout=5)

        ids = [o["option_id"] for o in request_event["options"]]
        assert ids == ["allowOnce", "allowSession", "deny"]
        always = next(o for o in request_event["options"] if o["option_id"] == "allowSession")
        assert always["content"] == '完整命令参数（精确匹配）：{"command": "del a.txt"}'

    asyncio.run(scenario())


def test_same_options_for_every_approver(store_db) -> None:
    """所有审批回调使用同样三项，不暴露模式或永久授权。"""
    async def scenario():
        store.ensure_session(store_db, "s1", "test-model", "删文件")
        recorder = _recorder(store_db)
        recorder.turn_id = "t1"
        queue: asyncio.Queue = asyncio.Queue()
        approver = InteractiveApprover(store_db, "s1", recorder, queue)

        wait_task = asyncio.create_task(approver(
            "bash", {"command": "echo hi"}, "需要确认"))
        request_event = await asyncio.wait_for(queue.get(), timeout=5)
        approvals.resolve(request_event["request_id"], "allowOnce", session_id="s1")
        await asyncio.wait_for(wait_task, timeout=5)

        ids = [o["option_id"] for o in request_event["options"]]
        assert ids == ["allowOnce", "allowSession", "deny"]

    asyncio.run(scenario())


@pytest.mark.parametrize("option", ["allowAlways", "fullAccess", "weird", "", "approve"])
def test_removed_or_unknown_option_has_no_effect(client, option) -> None:
    """旧/未知选项失败且保留待决请求，不写规则、不改模式、不会变成拒绝应答。"""
    from app.config import settings
    from app.sessions import execution_state, session_permissions

    sid = client.post("/api/sessions").json()["id"]
    slot = approvals.register("r-invalid", "bash", {"command": "echo hi"}, session_id=sid)
    response = client.post(f"/api/sessions/{sid}/approval", json={
        "request_id": "r-invalid", "option_id": option, "approved": True})
    assert response.status_code == 400
    assert approvals.get_request("r-invalid") is slot
    assert not slot["approved"] and not slot["event"].is_set()
    assert not execution_state.load_permission_rules(settings.data_dir)["allow"]
    assert execution_state.load_execution_state(settings.data_dir)["mode"] == "build"
    assert not session_permissions._grants
