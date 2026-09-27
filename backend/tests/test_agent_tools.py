"""BE-4 验证（工具层+权限闸门）。

执行类测试遵守 docs/RELIABILITY.md 白名单：bash 只跑 `echo` 安全命令；
文件操作全部发生在 tests/.tmp-data/ 的 workspace 沙箱内。
"""

import pytest

from app.agent import permissions
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


# ---------- 权限闸门 ----------


def test_permission_deny_list() -> None:
    """绝对禁止清单硬拒。"""
    decision, reason = permissions.check("bash", {"command": "rm -rf /"})
    assert decision == "deny"


def test_permission_delete_needs_approval() -> None:
    """删除类命令 → approve（交互审批）。"""
    decision, reason = permissions.check("bash", {"command": "Remove-Item a.txt"})
    assert decision == "approve" and "删除" in (reason or "")
    decision2, _ = permissions.check("bash", {"command": "del a.txt"})
    assert decision2 == "approve"


def test_permission_high_risk_needs_approval() -> None:
    """高危词 → approve。"""
    decision, _ = permissions.check("bash", {"command": "echo x | bash"})
    assert decision == "approve"


def test_permission_out_of_bounds_write_denied() -> None:
    """越界写硬拒不询问（mini_harness 规则）。"""
    decision, _ = permissions.check("write_file", {"path": "../evil.txt", "content": "x"})
    assert decision == "deny"


def test_permission_readonly_auto_allow() -> None:
    """只读工具与安全命令自动放行。"""
    assert permissions.check("read_file", {"path": "a.txt"})[0] == "allow"
    assert permissions.check("bash", {"command": "echo hi"})[0] == "allow"
