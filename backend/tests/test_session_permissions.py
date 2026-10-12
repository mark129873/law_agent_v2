"""会话授权安全边界：仅隔离空库、假模型/工具，不调用真实模型或危险命令。"""

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.agent.loop import TurnDeps, run_turn
from app.agent.tools import safe_path, workspace_root
from app.api.sessions import ApprovalIn, submit_approval
from app.config import settings
from app.models import LocalSetting
from app.sessions import approvals, execution_state, replay, session_permissions, store
from app.sessions.recorder import TurnRecorder


def _deps(session_id, approver=None):
    async def refuse(*args):
        return {"approved": False, "denial_reason": "需要再次确认"}
    return SimpleNamespace(recorder=SimpleNamespace(session_id=session_id), stop_flag=lambda: False,
                           approver=approver or refuse)


async def _allow_session(store_db, session_id="s", tool="write_file", tool_input=None):
    """走真实等待/API链路创建授权，避免测试直接注入权限掩盖接线问题。"""
    tool_input = tool_input or {"path": "approved.txt", "content": "a"}
    recorder = TurnRecorder(store_db, session_id, "fake", 1000)
    recorder.begin_turn("测试审批")
    queue = asyncio.Queue()
    task = asyncio.create_task(approvals.InteractiveApprover(store_db, session_id, recorder, queue)(
        tool, tool_input, "需要确认"))
    event = await asyncio.wait_for(queue.get(), 1)
    result = await submit_approval(session_id, ApprovalIn(request_id=event["request_id"], option_id="allowSession"))
    assert result == {"ok": True}
    assert (await asyncio.wait_for(task, 1))["approved"]
    recorder.end_turn("success")
    return event


@pytest.mark.parametrize("option", ["allowOnce", "allowSession", "deny"])
def test_three_choices_never_write_local_settings(store_db, monkeypatch, option):
    """已有mode/ruleset逐字段不变；新审批即使允许也绝不调用配置写入口。"""
    execution_state.save_execution_state(settings.data_dir, "build")
    execution_state.add_permission_rule(settings.data_dir, "allow", "bash", "legacy:*")
    before = [dict(row.__dict__) for row in store_db.scalars(select(LocalSetting))]
    before = [{k: v for k, v in row.items() if k != "_sa_instance_state"} for row in before]
    monkeypatch.setattr(execution_state, "_write", lambda *a, **k: pytest.fail("审批不能写项目配置"))

    async def scenario():
        recorder = TurnRecorder(store_db, "s", "fake", 1000); recorder.begin_turn("test")
        queue = asyncio.Queue()
        callback = approvals.InteractiveApprover(store_db, "s", recorder, queue)
        task = asyncio.create_task(callback("write_file", {"path": "a.txt", "content": "one"}, "写文件"))
        event = await queue.get()
        assert [item["option_id"] for item in event["options"]] == ["allowOnce", "allowSession", "deny"]
        await submit_approval("s", ApprovalIn(request_id=event["request_id"], option_id=option, feedback="只阅读"))
        result = await task
        assert result["approved"] is (option != "deny")
        assert session_permissions.allows("s", "write_file", {"path": "a.txt", "content": "two"}) is (option == "allowSession")
        if option == "deny":
            assert "只阅读" in result["denial_reason"]

    asyncio.run(scenario())
    store_db.expire_all()
    after = [{k: v for k, v in row.__dict__.items() if k != "_sa_instance_state"}
             for row in store_db.scalars(select(LocalSetting))]
    assert before == after


@pytest.mark.parametrize("first_is_child", [False, True])
def test_same_session_reuses_across_turns_and_child_only(store_db, monkeypatch, first_is_child):
    """真实主/子循环跨轮复用；另一会话仍挂起，拒绝不能写文件。"""
    class FakeClient:
        def __init__(self, content, child):
            self.calls = 0; self.content = content; self.child = child
        async def stream(self, **kwargs):
            self.calls += 1
            if self.child and self.calls == 1:
                yield {"type": "tool_use", "id": "child", "name": "subtask", "input": {"goal": "写说明"}}
            elif self.calls == (2 if self.child else 1):
                yield {"type": "tool_use", "id": "write", "name": "write_file",
                       "input": {"path": "./approved.txt", "content": self.content}}
                if self.content == "first":
                    # 同一模型响应内的第二次同目标调用也应复用第一次会话批准。
                    yield {"type": "tool_use", "id": "repeat-write", "name": "write_file",
                           "input": {"path": "approved.txt", "content": self.content}}
            else:
                yield {"type": "text_delta", "text": "已完成"}

    async def turn(sid, content, child, expected_option):
        queue = asyncio.Queue()
        recorder = TurnRecorder(store_db, sid, "fake", 1000)
        deps = TurnDeps(client=FakeClient(content, child), recorder=recorder, system_prompt="fake",
                        history=[{"role": "user", "content": "写说明"}], queue=queue,
                        approver=approvals.InteractiveApprover(store_db, sid, recorder, queue))
        async def consume():
            return [event async for event in run_turn(deps)]
        task = asyncio.create_task(consume())
        if expected_option:
            while True:
                event = await asyncio.wait_for(queue.get(), 1)
                if event["type"] == "approval_request":
                    break
            assert [o["option_id"] for o in event["options"]] == ["allowOnce", "allowSession", "deny"]
            await submit_approval(sid, ApprovalIn(request_id=event["request_id"], option_id=expected_option))
        events = await asyncio.wait_for(task, 2)
        assert events[-1]["state"] == "success"
        assert not any(e["type"] == "approval_request" for e in list(queue._queue))

    async def scenario():
        await turn("s", "first", first_is_child, "allowSession")
        assert safe_path("approved.txt").read_text() == "first"
        await turn("s", "second", not first_is_child, None)
        assert safe_path("approved.txt").read_text() == "second"
        await turn("other", "must not write", False, "deny")
        assert safe_path("approved.txt").read_text() == "second"
        assert "other" not in session_permissions._grants
        assert len(replay.load_replay(store_db, "s")["turns"]) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("tool", ["write_file", "edit_file", "delete_file", "read_file"])
def test_file_scope_normalizes_target_but_not_other_tools_or_paths(tmp_data_dir, tool):
    """相对/绝对/点段指向同一目标；兄弟目标、子路径、工具变化都不继承。"""
    target = safe_path("folder/approved.txt")
    session_permissions.grant("s", session_permissions.scope_for(tool, {"path": str(target)}))
    for path in ("folder/approved.txt", "./folder/approved.txt", "folder/../folder/approved.txt"):
        assert session_permissions.allows("s", tool, {"path": path})
    for path in ("folder/other.txt", "folder/approved.txt/child", "../outside.txt"):
        assert not session_permissions.allows("s", tool, {"path": path})
    assert not session_permissions.allows("s", "other_tool", {"path": str(target)})
    assert not session_permissions.allows("s", tool, {"path": str(target), "new_flag": True})
    assert not session_permissions.allows("other", tool, {"path": str(target)})


def test_symlink_retarget_does_not_reuse_old_authorization(tmp_data_dir):
    """解析符号链接后的目标才是范围；之后改指向不能继承原目标权限。"""
    root = workspace_root(); root.mkdir(parents=True, exist_ok=True)
    alias = root / "alias"; alias.symlink_to(root / "one")
    session_permissions.grant("s", session_permissions.scope_for("write_file", {"path": "alias"}))
    assert session_permissions.allows("s", "write_file", {"path": "one"})
    alias.unlink(); alias.symlink_to(root / "two")
    assert not session_permissions.allows("s", "write_file", {"path": "alias"})
    alias.unlink(); alias.symlink_to(tmp_data_dir / "outside")
    assert not session_permissions.allows("s", "write_file", {"path": "alias"})


@pytest.mark.parametrize("suffix", [" --extra", "; echo other", " && echo other", " | echo other", "\necho other",
                                  "\r\necho other", " $(echo other)", " `echo other`", " > other.txt", "\n"])
def test_command_allow_is_exact_never_a_prefix(tmp_data_dir, suffix):
    command = "npm run build"
    session_permissions.grant("s", session_permissions.scope_for("bash", {"command": command}))
    assert session_permissions.allows("s", "bash", {"command": command})
    assert not session_permissions.allows("s", "bash", {"command": command + suffix})
    assert not session_permissions.allows("s", "bash", {"command": command, "cwd": "elsewhere"})


@pytest.mark.parametrize("command", ["echo one; echo two", "echo one\necho two", "echo $(Get-Content a)", "echo `n"])
def test_approved_complex_command_only_matches_identical_input(tmp_data_dir, command):
    session_permissions.grant("s", session_permissions.scope_for("bash", {"command": command}))
    assert session_permissions.allows("s", "bash", {"command": command})
    assert not session_permissions.allows("s", "bash", {"command": command + "; echo changed"})
    assert not session_permissions.allows("s", "bash", {"command": command.upper()})


def test_other_tools_include_all_nested_input_fields(tmp_data_dir):
    original = {"url": "https://example.invalid", "options": {"method": "GET", "query": [1, 2]}}
    session_permissions.grant("s", session_permissions.scope_for("custom", original))
    reordered = {"options": {"query": [1, 2], "method": "GET"}, "url": original["url"]}
    assert session_permissions.allows("s", "custom", reordered)
    for changed in ({"url": original["url"]}, {**original, "extra": True},
                    {**original, "options": {"method": "POST", "query": [1, 2]}}):
        assert not session_permissions.allows("s", "custom", changed)
    assert not session_permissions.allows("s", "custom", {})


@pytest.mark.parametrize("mode", ["build", "edit", "yolo"])
def test_hard_deny_remains_above_every_session_grant(tmp_data_dir, mode):
    execution_state.save_execution_state(tmp_data_dir, mode)
    tool_input = {"command": "rm -rf /"}
    session_permissions.grant("s", session_permissions.scope_for("bash", tool_input))
    result = asyncio.run(approvals.authorize_tool(_deps("s"), "bash", tool_input))
    assert "安全策略拒绝" in result


@pytest.mark.parametrize("mode", ["build", "edit"])
def test_existing_deny_result_beats_session_allow(tmp_data_dir, mode):
    execution_state.save_execution_state(tmp_data_dir, mode)
    tool_input = {"path": "approved.txt", "content": "one"}
    session_permissions.grant("s", session_permissions.scope_for("write_file", tool_input))
    execution_state.add_permission_rule(tmp_data_dir, "deny", "write_file", "approved.txt")
    result = asyncio.run(approvals.authorize_tool(_deps("s"), "write_file", tool_input))
    assert "安全策略拒绝" in result


@pytest.mark.parametrize("option", ["allowOnce", "allowSession", "deny"])
def test_cross_session_and_replayed_requests_fail_without_change(store_db, option):
    async def scenario():
        recorder = TurnRecorder(store_db, "owner", "fake", 1000); recorder.begin_turn("test")
        queue = asyncio.Queue()
        task = asyncio.create_task(approvals.InteractiveApprover(store_db, "owner", recorder, queue)(
            "write_file", {"path": "a.txt"}, "写入"))
        event = await queue.get(); rid = event["request_id"]
        with pytest.raises(HTTPException) as error:
            await submit_approval("other", ApprovalIn(request_id=rid, option_id=option))
        assert error.value.status_code == 404
        assert not session_permissions._grants
        assert approvals.get_request(rid) and not approvals.get_request(rid)["event"].is_set()
        await submit_approval("owner", ApprovalIn(request_id=rid, option_id=option))
        await task
        before = {sid: set(scopes) for sid, scopes in session_permissions._grants.items()}
        with pytest.raises(HTTPException) as error:
            await submit_approval("owner", ApprovalIn(request_id=rid, option_id="allowSession"))
        assert error.value.status_code == 404
        assert session_permissions._grants == before
        assert "other" not in before
    asyncio.run(scenario())


def test_refresh_and_restart_lifetimes_are_separate(store_db, client):
    """新页面读取不清内存；全新解释器读取同库时会话授权消失，旧项目配置仍在。"""
    asyncio.run(_allow_session(store_db))
    assert client.get("/api/sessions/s").status_code == 200
    assert session_permissions.allows("s", "write_file", {"path": "approved.txt", "content": "later"})
    execution_state.save_execution_state(settings.data_dir, "edit")
    execution_state.add_permission_rule(settings.data_dir, "allow", "bash", "legacy:*")
    code = '''import json
from app.config import settings
from app.sessions import execution_state, session_permissions
print(json.dumps({"grants": session_permissions._grants, "state": execution_state.load_execution_state(settings.data_dir), "rules": execution_state.load_permission_rules(settings.data_dir)}))'''
    completed = subprocess.run([sys.executable, "-c", code], check=True, text=True, capture_output=True, cwd=Path(__file__).resolve().parents[1])
    data = json.loads(completed.stdout)
    assert data["grants"] == {}
    assert data["state"] == {"mode": "edit"}
    assert data["rules"]["allow"] == [{"toolName": "bash", "ruleContent": "legacy:*"}]


def test_delete_session_clears_events_pending_and_grants(store_db, client):
    asyncio.run(_allow_session(store_db))
    approvals.register("synthetic-pending", "write_file", {"path": "b.txt"}, session_id="s")
    assert client.delete("/api/sessions/s").status_code == 200
    assert "s" not in session_permissions._grants and "s" not in approvals._events
    assert approvals.get_request("synthetic-pending") is None
    assert client.get("/api/sessions/s").status_code == 404
    assert not session_permissions.allows("s", "write_file", {"path": "approved.txt"})


def test_forget_turn_preserves_only_session_scope(store_db):
    event = asyncio.run(_allow_session(store_db))
    record = approvals.approval_events("s")[0]
    approvals.forget_turn("s", record["turn_id"])
    assert not approvals.approval_events("s")
    assert approvals.get_request(event["request_id"]) is None
    assert session_permissions.allows("s", "write_file", {"path": "approved.txt"})


@pytest.mark.parametrize("change", ["cancel", "delete", "deny_rule", "retarget", "input"])
def test_approval_invalidated_before_waiter_resumes_cannot_grant(store_db, change):
    """批准应答先到也不抢写内存；取消/删除/新增拒绝/路径改指向均不留下授权。"""
    async def scenario():
        root = workspace_root(); root.mkdir(parents=True, exist_ok=True)
        alias = root / "alias"; alias.symlink_to(root / "target")
        recorder = TurnRecorder(store_db, "s", "fake", 1000); recorder.begin_turn("test")
        queue = asyncio.Queue()
        tool_input = {"path": "alias", "content": "已展示内容"}
        task = asyncio.create_task(approvals.InteractiveApprover(store_db, "s", recorder, queue)(
            "write_file", tool_input, "写入"))
        event = await queue.get()
        await submit_approval("s", ApprovalIn(request_id=event["request_id"], option_id="allowSession"))
        assert not session_permissions._grants
        if change == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            if change == "delete":
                approvals.forget_session("s")
            elif change == "deny_rule":
                execution_state.add_permission_rule(settings.data_dir, "deny", "write_file", "alias")
            elif change == "input":
                tool_input["content"] = "未展示的新内容"
            else:
                alias.unlink(); alias.symlink_to(root / "different")
            assert not (await task)["approved"]
        assert not session_permissions._grants
        assert approvals.get_request(event["request_id"]) is None
    asyncio.run(scenario())


def test_invalid_file_scope_and_missing_decision_keep_request_pending(client):
    """不能保存无目标的会话范围；缺少决定不当作拒绝或批准。"""
    slot = approvals.register("invalid", "write_file", {}, session_id="s")
    for body in ({"request_id": "invalid", "option_id": "allowSession"}, {"request_id": "invalid"}):
        result = client.post("/api/sessions/s/approval", json=body)
        assert result.status_code == 400
        assert approvals.get_request("invalid") is slot and not slot["event"].is_set()
        assert not session_permissions._grants


def test_option_label_and_grant_share_one_path_snapshot(tmp_data_dir, monkeypatch):
    """每次登记只解析一次范围，选项展示与稍后保存不会使用两个不同目标。"""
    original = session_permissions.scope_for
    calls = []
    def tracked(tool, tool_input):
        calls.append((tool, tool_input))
        return original(tool, tool_input)
    monkeypatch.setattr(session_permissions, "scope_for", tracked)
    slot = approvals.register("scope-snapshot", "write_file", {"path": "a.txt"}, session_id="s")
    assert len(calls) == 1
    assert slot["options"][1]["content"] == session_permissions.describe(slot["scope"])
