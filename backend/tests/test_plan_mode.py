"""计划模式验证：enter/exit 工具流、计划文件落盘、系统提示词注入、状态端点。"""

from app.agent.permission_service import check_permission
from app.agent.tools import tool_enter_plan_mode, tool_exit_plan_mode
from app.config import settings


def test_plan_tools_flow(tmp_data_dir) -> None:
    """enter 置位 → exit 写计划文件并复位；模式保持不变；重复 exit 报 Error。"""
    assert tool_enter_plan_mode().startswith("已进入计划模式")
    state = __import__("app.sessions.execution_state", fromlist=["load_execution_state"]).load_execution_state(settings.data_dir)
    assert state["plan_enabled"] is True and state["mode"] == "build"

    plan = "# 计划\n1. 先做 A\n2. 再做 B"
    out = tool_exit_plan_mode(plan)
    assert out.startswith("用户已批准计划")
    files = list((settings.data_dir / "plans").glob("plan-*.md"))
    assert len(files) == 1 and files[0].read_text(encoding="utf-8") == plan

    state = __import__("app.sessions.execution_state", fromlist=["load_execution_state"]).load_execution_state(settings.data_dir)
    assert state["plan_enabled"] is False and state["mode"] == "build"  # mode 保持原值

    out2 = tool_exit_plan_mode(plan)  # 已不在计划模式
    assert out2.startswith("Error:")


def test_plan_mode_evaluation() -> None:
    """plan 检查分支：enter 免确认 / exit 必问 / 只读放行 / 写入 DENY。"""
    rules = {"version": 1}
    assert check_permission("build", False, rules, "enter_plan_mode", {})["decision"] == "allow"
    assert check_permission("build", True, rules, "exit_plan_mode", {"plan": "x"})["decision"] == "ask"
    assert check_permission("build", True, rules, "bash", {"command": "echo hi"})["decision"] == "allow"
    denied = check_permission("build", True, rules, "delete_file", {"path": "a.txt"})
    assert denied["decision"] == "deny" and denied["rule_id"] == "mode.plan.nonReadOnly"
    # yolo 不绕过 plan
    assert check_permission("yolo", True, rules, "write_file", {"path": "a.txt"})["decision"] == "deny"


def test_system_prompt_plan_section() -> None:
    """计划模式启用时系统提示词追加约束段。"""
    from app.api.sessions import _load_system_prompt

    assert "计划模式（已启用）" in _load_system_prompt(True)
    assert "计划模式（已启用）" not in _load_system_prompt(False)


def test_permission_state_endpoint(client) -> None:
    """状态查询端点：返回当前模式与计划标志。"""
    assert client.get("/api/permission/state").json() == {"mode": "build", "plan_enabled": False}
