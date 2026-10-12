"""审批取消回归：只用假模型、假工具，检查等待与迟到响应边界。"""

import asyncio

import pytest
from fastapi import HTTPException

from app.agent.loop import TurnDeps, run_turn
from app.api.sessions import ApprovalIn, submit_approval
from app.config import settings
from app.sessions import approvals, execution_state, replay
from app.sessions.approvals import InteractiveApprover
from app.sessions.recorder import TurnRecorder


@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("race", ["stop", "stop_then_allow", "allow_then_stop"])
def test_waiting_approval_stops_without_executing(store_db, monkeypatch, child, race):
    """主/子工具等待均立即退出；同一事件循环tick同时批准与停止时停止优先。"""
    executed = []
    monkeypatch.setattr("app.agent.loop.execute_tool", lambda *args: executed.append(args) or "ok")
    monkeypatch.setattr("app.agent.subtask.execute_tool", lambda *args: executed.append(args) or "ok")

    class FakeClient:
        calls = 0

        async def stream(self, **kwargs):
            self.calls += 1
            if child and self.calls == 1:
                yield {"type": "tool_use", "id": "sub", "name": "subtask", "input": {"goal": "写文件"}}
            else:
                yield {"type": "tool_use", "id": "write", "name": "write_file",
                       "input": {"path": "never.txt", "content": "never"}}

    async def scenario():
        queue = asyncio.Queue()
        stop = asyncio.Event()
        recorder = TurnRecorder(store_db, "cancel", model="fake", max_tokens=1000)
        fake = FakeClient()
        deps = TurnDeps(client=fake, recorder=recorder, system_prompt="fake",
                        history=[{"role": "user", "content": "写文件"}], queue=queue,
                        stop_flag=stop.is_set,
                        approver=InteractiveApprover(store_db, "cancel", recorder, queue, stop))

        async def consume():
            return [event async for event in run_turn(deps)]

        task = asyncio.create_task(consume())
        while True:
            event = await asyncio.wait_for(queue.get(), 1)
            if event["type"] == "approval_request":
                break
        request_id = event["request_id"]
        if race == "allow_then_stop":
            assert approvals.resolve(request_id, True)
        stop.set()
        if race == "stop_then_allow":
            assert not approvals.resolve(request_id, True)
        events = await asyncio.wait_for(task, 1)
        assert events[-1]["state"] == "stopped"
        assert not executed
        assert fake.calls == (2 if child else 1)
        assert approvals.get_request(request_id) is None
        assert not approvals.resolve(request_id, True)
        assert replay.load_replay(store_db, "cancel")["pending_approval"] is None
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())


@pytest.mark.parametrize("option", ["allowOnce", "fullAccess", "allowAlways"])
def test_late_api_approval_cannot_persist_authorization(store_db, option):
    """停止已置位但等待任务未调度时，API也须拒绝，不能先写yolo/规则。"""
    async def scenario():
        stop = asyncio.Event()
        approvals.register("late", "bash", {"command": "echo hi"}, full_access=True, stop_event=stop)
        stop.set()
        with pytest.raises(HTTPException) as exc:
            await submit_approval("cancel", ApprovalIn(request_id="late", option_id=option))
        assert exc.value.status_code == 404
        assert execution_state.load_execution_state(settings.data_dir)["mode"] == "build"
        assert not execution_state.load_permission_rules(settings.data_dir)["allow"]
        assert "late" not in approvals._pending
    asyncio.run(scenario())


def test_cancelled_waiter_cleans_slot_and_replay(store_db):
    """协程取消也清理全部等待任务及requested状态，不留失效弹窗。"""
    async def scenario():
        recorder = TurnRecorder(store_db, "cancel-task", model="fake", max_tokens=1000)
        recorder.begin_turn("test")
        queue = asyncio.Queue()
        approver = InteractiveApprover(store_db, "cancel-task", recorder, queue, asyncio.Event())
        task = asyncio.create_task(approver("write_file", {"path": "a"}, "写入"))
        event = await queue.get()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert event["request_id"] not in approvals._pending
        assert replay.load_replay(store_db, "cancel-task")["pending_approval"] is None
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())
