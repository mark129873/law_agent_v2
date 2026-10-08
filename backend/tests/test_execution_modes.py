"""精简执行模式回归：不保留Plan兼容层，普通模式、任务板与审批仍可用。"""

import json

import pytest

from app.api.sessions import TurnIn, _load_system_prompt, get_llm_client
from app.agent.permission_service import TOOL_SPECS, check_permission
from app.agent.tools import TOOLS, TOOL_HANDLERS, execute_tool
from app.config import settings
from app.sessions import approvals, execution_state


def test_tool_registry_contains_only_current_tools() -> None:
    """主助手九工具、子助手七同步工具；没有无用的计划工具或能力声明。"""
    names = {tool["name"] for tool in TOOLS}
    assert names == {"bash", "read_file", "write_file", "edit_file", "glob",
                     "delete_file", "load_skill", "todo_write", "subtask"}
    assert set(TOOL_HANDLERS) == names - {"todo_write", "subtask"}
    assert set(TOOL_SPECS) == names
    assert execute_tool("enter_plan_mode", {}).startswith("Error: 未知工具")
    assert execute_tool("exit_plan_mode", {"plan": "旧计划"}).startswith("Error: 未知工具")


def test_prompt_and_turn_contract() -> None:
    """请求仅含正文与模式；提示词原样读取，不再追加计划模式工作流。"""
    from app.api.sessions import _SYSTEM_PROMPT_PATH

    assert set(TurnIn.model_fields) == {"text", "mode"}
    assert _load_system_prompt() == _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["build", "edit", "yolo"])
def test_execution_state_roundtrip(client, mode) -> None:
    """空库默认build，三种模式持久化与端点回显只含mode。"""
    assert client.get("/api/permission/state").json() == {"mode": "build"}
    assert execution_state.save_execution_state(settings.data_dir, mode) == {"mode": mode}
    assert client.get("/api/permission/state").json() == {"mode": mode}
    saved = json.loads((settings.data_dir / "execution_state.json").read_text())
    assert saved == {"mode": mode}
    assert not (settings.data_dir / "plans").exists()


def test_build_keeps_ordinary_approval_and_todo(tmp_data_dir) -> None:
    """移除Plan不把build变成只读，也不移除todo/子助手/普通文件审批。"""
    for tool in ("todo_write", "subtask", "read_file"):
        assert check_permission("build", {}, tool, {"path": "a.txt"})["decision"] == "allow"
    assert check_permission("build", {}, "write_file", {"path": "a.txt"})["decision"] == "ask"
    assert [o["option_id"] for o in approvals.build_options("write_file", {})] == [
        "allowOnce", "fullAccess", "deny"]


def test_removed_approval_option_cannot_authorize(client) -> None:
    """旧计划的approve不再是授权选项，按普通未知选项拒绝。"""
    slot = approvals.register("removed-plan-option", "write_file", {"path": "a.txt"})
    response = client.post("/api/sessions/test/approval", json={
        "request_id": "removed-plan-option", "option_id": "approve"})
    assert response.json() == {"ok": True}
    assert slot["approved"] is False


@pytest.mark.parametrize("mode", ["build", "edit", "yolo"])
def test_submit_mode_preserves_todo_and_history(client, monkeypatch, mode) -> None:
    """假模型完成todo往返，验证三模式提交、空库建会话与普通历史回放。"""
    monkeypatch.setattr(settings, "anthropic_api_key", "offline-test")
    monkeypatch.setattr(settings, "model_id", "offline-test")

    class TodoClient:
        calls = 0

        async def stream(self, *, system, messages, tools):
            self.calls += 1
            assert {tool["name"] for tool in tools} == set(TOOL_SPECS)
            if self.calls == 1:
                yield {"type": "tool_use", "id": "todo-test", "name": "todo_write",
                       "input": {"items": [{"content": "测试任务", "status": "completed"}]}}
            else:
                yield {"type": "text_delta", "text": "已记录任务"}

    fake = TodoClient()
    client.app.dependency_overrides[get_llm_client] = lambda: fake
    sid = client.post("/api/sessions").json()["id"]
    response = client.post(f"/api/sessions/{sid}/turn", json={"text": "记录任务", "mode": mode})
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["state"] == "success"
    assert any(event["type"] == "todo_updated" for event in events)
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["turns"][0]["final_text"] == "已记录任务"
    assert client.get("/api/permission/state").json() == {"mode": mode}
    assert len(client.get("/api/sessions").json()) == 1


@pytest.mark.parametrize("approved", [True, False])
def test_build_write_obeys_ordinary_approval(store_db, approved) -> None:
    """假模型发起真实沙箱写入，build仍需普通审批；拒绝时不能写文件。"""
    import asyncio
    from app.agent.loop import TurnDeps, run_turn
    from app.agent.tools import workspace_root
    from app.sessions.recorder import TurnRecorder

    class WriteClient:
        calls = 0

        async def stream(self, *, system, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield {"type": "tool_use", "id": "ordinary-write", "name": "write_file",
                       "input": {"path": "approval.txt", "content": "已批准"}}
            else:
                yield {"type": "text_delta", "text": "完成"}

    calls = []

    async def approve(tool, tool_input, reason):
        calls.append(tool)
        return {"approved": approved, "denial_reason": None if approved else "只阅读"}

    deps = TurnDeps(client=WriteClient(), recorder=TurnRecorder(store_db, "ordinary-write", model="offline-test", max_tokens=1000),
                    system_prompt="离线测试", history=[{"role": "user", "content": "写文件"}],
                    approver=approve)

    async def scenario():
        return [event async for event in run_turn(deps)]

    events = asyncio.run(scenario())
    assert events[-1]["state"] == "success"
    assert calls == ["write_file"]
    target = workspace_root() / "approval.txt"
    assert target.exists() is approved
    if approved:
        assert target.read_text() == "已批准"
