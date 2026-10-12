"""命令分类只运行纯判定，不实际执行攻击字符串。"""

import pytest

from app.agent.permission_service import check_permission


@pytest.mark.parametrize("mode", ["build", "edit"])
@pytest.mark.parametrize("command", [
    "echo hello\nSet-Content audit.txt changed", "echo hello\rSet-Content audit.txt changed",
    "echo $(Set-Content audit.txt changed)", "echo $env:PATH", "echo `whoami`",
    "echo (New-Item audit.txt)", "echo hi; Set-Content audit.txt changed",
    "echo hi | Set-Content audit.txt", "echo hi > audit.txt", "echo hi\n",
    "git branch -D audit", "git branch audit", "git branch -m renamed",
    "git remote remove origin", "git remote add origin elsewhere",
    "git log --output=audit.txt", "git log -p", "git show --ext-diff",
    "git diff --output audit.txt", "git -c alias.x=whoami x",
    "python --version evil.py", "node --version; whoami", "echo 'unfinished",
])
def test_complex_or_writing_command_requires_approval(mode, command):
    """根命令看似只读也不能绕过审批；未知语法一律保守处理。"""
    assert check_permission(mode, {}, "bash", {"command": command})["decision"] == "ask"


@pytest.mark.parametrize("command", [
    "echo hello", "  Get-Content notes.txt  ", "pwd", "ls -la", "cat ./notes.txt",
    "git status", "git status --short --branch", "git log --oneline -5",
    "git branch", "git branch --list", "git branch -a -vv", "git remote -v",
    "python --version", "node --version",
])
def test_known_simple_reads_stay_approval_free(command):
    """常用字面量读取仍免批，避免把修复变成全量禁用。"""
    assert check_permission("build", {}, "bash", {"command": command})["decision"] == "allow"
