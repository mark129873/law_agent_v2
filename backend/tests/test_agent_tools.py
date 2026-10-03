"""BE-4 验证（工具层+权限闸门）。

执行类测试遵守 docs/RELIABILITY.md 白名单：bash 只跑 `echo` 安全命令；
文件操作全部发生在 tests/.tmp-data/ 的 workspace 沙箱内。
"""

import pytest

from app.agent.permission_service import check_permission, derive_rule
from app.agent.tools import (
    execute_tool,
    safe_path,
    tool_bash,
    tool_delete_file,
    tool_edit_file,
    tool_glob,
    tool_read_file,
    tool_write_file,
    workspace_root,
)


@pytest.fixture(autouse=True)
def _workspace(tmp_data_dir):
    """确保沙箱目录存在（真实启动由 lifespan 建，测试里手动建）。"""
    workspace_root().mkdir(parents=True, exist_ok=True)


# ---------- safe_path 沙箱 ----------


def test_safe_path_allows_inside() -> None:
    """沙箱内相对路径正常解析。"""
    p = safe_path("sub/dir/a.txt")
    assert p.is_absolute() and str(workspace_root()) in str(p)


def test_safe_path_rejects_escape() -> None:
    """越界路径（.. 与盘符绝对路径）一律拒绝。"""
    with pytest.raises(ValueError):
        safe_path("../outside.txt")
    with pytest.raises(ValueError):
        safe_path("C:/Windows/system32/config")


# ---------- 文件工具（真实读写，仅限沙箱） ----------


def test_write_read_edit_glob_roundtrip() -> None:
    """写→读→改→列 全链路。"""
    assert "已写入" in tool_write_file("docs/a.md", "# 标题\n内容")
    assert tool_read_file("docs/a.md") == "# 标题\n内容"

    # edit_file：恰好一次匹配才允许替换
    assert "Error" in tool_edit_file("docs/a.md", "不存在的片段", "x")
    tool_write_file("docs/b.md", "same\nsame")
    assert "Error" in tool_edit_file("docs/b.md", "same", "diff")  # 出现两次，拒绝
    assert "已编辑" in tool_edit_file("docs/a.md", "内容", "正文")
    assert tool_read_file("docs/a.md") == "# 标题\n正文"

    # glob：相对路径输出
    assert "docs/a.md" in tool_glob("**/*.md")


def test_read_missing_file() -> None:
    """读不存在的文件返回 Error 字符串而非抛异常。"""
    assert tool_read_file("nope.txt").startswith("Error:")


def test_delete_file_moves_to_rubbish() -> None:
    """删除 = 移入 .rubbish/ 可找回。"""
    tool_write_file("tmp/x.txt", "bye")
    result = tool_delete_file("tmp/x.txt")
    assert "已删除" in result and ".rubbish" in result
    assert tool_read_file("tmp/x.txt").startswith("Error:")
    rubbish = list((workspace_root() / ".rubbish").glob("*_x.txt"))
    assert len(rubbish) == 1 and rubbish[0].read_text(encoding="utf-8") == "bye"


def test_bash_echo_whitelist(tmp_path) -> None:
    """bash 白名单安全命令：echo 可跑且 cwd 在沙箱内。"""
    output = tool_bash("echo harness-ok")
    assert "harness-ok" in output


def test_execute_tool_unknown_and_bad_args() -> None:
    """统一入口兜底：未知工具/参数不匹配都转 Error 字符串。"""
    assert execute_tool("nope", {}).startswith("Error:")
    assert execute_tool("write_file", {"path": "x"}) .startswith("Error:")


# ---------- 权限服务（ZCode 复刻，docs/ARCHITECTURE.md §4.2） ----------


def test_permission_deny_list() -> None:
    """绝对禁止清单硬拒（任何模式/规则不可越过）。"""
    for mode in ("build", "edit", "yolo"):
        result = check_permission(mode, False, {"version": 1}, "bash", {"command": "rm -rf /"})
        assert result["decision"] == "deny" and result["rule_id"] == "hard.deny"


def test_permission_out_of_bounds_write_denied() -> None:
    """越界写硬拒不询问（mini_harness 规则）。"""
    result = check_permission("build", False, {"version": 1}, "write_file", {"path": "../evil.txt"})
    assert result["decision"] == "deny"


def test_permission_build_matrix() -> None:
    """build 模式判定矩阵：只读放行 / bash 高危 ask / 写文件 ask / 任务板放行。"""
    rules = {"version": 1, "allow": [], "deny": []}
    assert check_permission("build", False, rules, "read_file", {"path": "a.txt"})["decision"] == "allow"
    bash_ask = check_permission("build", False, rules, "bash", {"command": "Remove-Item a.txt"})
    assert bash_ask["decision"] == "ask" and bash_ask["rule_id"] == "mode.build.highRisk"
    assert check_permission("build", False, rules, "write_file", {"path": "a.txt"})["decision"] == "ask"
    assert check_permission("build", False, rules, "todo_write", {"items": []})["decision"] == "allow"


def test_permission_bash_readonly_downgrade() -> None:
    """bash 只读命令运行时降级：build 免批；含管道不做降级仍 ask。"""
    rules = {"version": 1}
    assert check_permission("build", False, rules, "bash", {"command": "echo hi"})["decision"] == "allow"
    assert check_permission("build", False, rules, "bash", {"command": "git status"})["decision"] == "allow"
    assert check_permission("build", False, rules, "bash", {"command": "echo hi | bash"})["decision"] == "ask"


def test_permission_mode_semantics() -> None:
    """edit 放行文件编辑、删除仍 ask；yolo 全放。"""
    rules = {"version": 1}
    assert check_permission("edit", False, rules, "write_file", {"path": "a.txt"})["decision"] == "allow"
    assert check_permission("edit", False, rules, "delete_file", {"path": "a.txt"})["decision"] == "ask"
    assert check_permission("yolo", False, rules, "write_file", {"path": "a.txt"})["decision"] == "allow"


def test_permission_plan_mode() -> None:
    """plan：只读放行、写入直接拒绝（不弹窗）；EnterPlanMode 免确认；Exit 只在 plan 中 ask。"""
    rules = {"version": 1}
    assert check_permission("build", True, rules, "read_file", {"path": "a.txt"})["decision"] == "allow"
    denied = check_permission("build", True, rules, "write_file", {"path": "a.txt"})
    assert denied["decision"] == "deny" and denied["rule_id"] == "mode.plan.nonReadOnly"
    assert check_permission("build", False, rules, "enter_plan_mode", {})["decision"] == "allow"
    assert check_permission("build", False, rules, "exit_plan_mode", {"plan": "x"})["decision"] == "deny"
    assert check_permission("build", True, rules, "exit_plan_mode", {"plan": "x"})["decision"] == "ask"


def test_permission_rules_match_and_priority() -> None:
    """规则：deny 压过 allow；前缀匹配生效；allow 规则不可绕过计划模式。"""
    rules = {"version": 1,
             "allow": [{"tool": "bash", "content": "echo:*"}],
             "deny": [{"tool": "bash", "content": "echo secret*"}]}
    assert check_permission("build", False, rules, "bash", {"command": "echo hi"})["decision"] == "allow"
    assert check_permission("build", False, rules, "bash", {"command": "echo secret file"})["decision"] == "deny"
    # plan 下 allow 规则不生效（写入被 plan 检查拒绝）——规则不可绕过计划模式
    assert check_permission("build", True, rules, "write_file", {"path": "a.txt"})["decision"] == "deny"


def test_derive_rule_safety() -> None:
    """规则推导：高危根命令退化为整条精确，普通命令为首词前缀；非 bash 不推导。"""
    assert derive_rule("bash", {"command": "rm scratch.txt"}) == {"tool": "bash", "content": "rm scratch.txt"}
    assert derive_rule("bash", {"command": "pnpm run lint --fix"}) == {"tool": "bash", "content": "pnpm:*"}
    assert derive_rule("write_file", {"path": "a.txt"}) is None
