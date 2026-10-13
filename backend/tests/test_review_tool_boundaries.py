"""审查回归：项目规则、glob 沙箱和命令失败；仅隔离目录与假子进程。"""

import asyncio
import subprocess

import pytest

from app.agent.loop import TurnDeps, run_turn
from app.agent.permission_service import check_permission
from app.agent.tools import tool_bash, tool_glob, workspace_root
from app.sessions import replay
from app.sessions.recorder import TurnRecorder


@pytest.fixture(autouse=True)
def workspace(tmp_data_dir):
    """每测独立的工作区；外部链接目标也只建在 tests/.tmp-data 内。"""
    root = workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.parametrize("behavior,matched,unmatched", [
    ("allow", "allow", "ask"),
    ("deny", "deny", "ask"),
    ("ask", "ask", "allow"),
])
def test_project_wildcard_requires_entire_subject(behavior, matched, unmatched):
    """三个规则桶均要求后缀匹配；ask 用 edit 默认放行来证明命中差异。"""
    rules = {behavior: [{"toolName": "write_file", "ruleContent": "reports/*.md"}]}
    mode = "edit" if behavior == "ask" else "build"
    for path in ("reports/a.md", "reports/a.md.md"):
        assert check_permission(mode, rules, "write_file", {"path": path})["decision"] == matched
    for path in ("reports/a.md.exe", "reports/a.md/extra", "other/reports/a.md"):
        assert check_permission(mode, rules, "write_file", {"path": path})["decision"] == unmatched


def test_project_wildcards_keep_literals_and_multiple_stars():
    """只星号通配，正则/其他 glob 字符仍是字面量，不悄悄扩大既有规则语义。"""
    rules = {"allow": [{"toolName": "write_file", "ruleContent": "reports/*[a]?.*.md"}]}
    assert check_permission("build", rules, "write_file", {"path": "reports/x[a]?.draft.md"})["decision"] == "allow"
    for path in ("reports/xa1.draft.md", "reports/x[a]?.draft.md.exe"):
        assert check_permission("build", rules, "write_file", {"path": path})["decision"] == "ask"


def test_command_prefix_rule_keeps_word_boundary():
    """cmd:* 仍按命令词边界允许参数，不变成任意文本前缀或普通星号模式。"""
    rules = {"allow": [{"toolName": "bash", "ruleContent": "custom:*"}]}
    for command in ("custom", "custom one", "custom\tone"):
        assert check_permission("build", rules, "bash", {"command": command})["decision"] == "allow"
    for command in ("customized", "custom; echo other"):
        assert check_permission("build", rules, "bash", {"command": command})["decision"] == "ask"


@pytest.mark.parametrize("pattern", [
    "../outside.md", "docs/../../*.md", "docs/../*.md", r"..\outside.md",
    "/outside/*.md", r"C:\outside\*.md", "C:outside/*.md", r"\\server\share\*.md",
])
def test_glob_traversal_hard_denied_for_all_modes(pattern):
    """词法越界在权限入口和直接工具入口都拒绝，yolo/项目 allow 不能绕过。"""
    rules = {"allow": [{"toolName": "glob", "ruleContent": "*"}]}
    for mode in ("build", "edit", "yolo"):
        decision = check_permission(mode, rules, "glob", {"pattern": pattern})
        assert decision["decision"] == "deny" and decision["rule_id"] == "hard.deny"
    assert tool_glob(pattern).startswith("Error:")


def test_glob_normal_relative_and_recursive_patterns(workspace):
    """普通非递归/递归/Windows 分隔符查询都保留相对路径输出。"""
    (workspace / "docs" / "nested").mkdir(parents=True)
    (workspace / "docs" / "a.md").write_text("safe", encoding="utf-8")
    (workspace / "docs" / "nested" / "b.md").write_text("safe", encoding="utf-8")
    (workspace / "docs" / "ignore.txt").write_text("safe", encoding="utf-8")
    for pattern in ("docs/*.md", r"docs\*.md"):
        assert check_permission("build", {}, "glob", {"pattern": pattern})["decision"] == "allow"
        assert tool_glob(pattern) == "docs/a.md"
    assert tool_glob("**/*.md") == "docs/a.md\ndocs/nested/b.md"
    assert tool_glob("**/*.missing") == "(无匹配文件)"
    # 即使绝对路径恰好在工作区内，glob 的模式契约仍要求相对路径。
    assert tool_glob(str(workspace / "docs" / "*.md")).startswith("Error:")


def test_glob_explicit_outside_symlink_hard_denied(tmp_data_dir, workspace):
    """已知链接前缀在枚举前就拒绝，不因完全访问模式而允许越界。"""
    outside = tmp_data_dir / "outside"
    outside.mkdir()
    (outside / "private.md").write_text("private", encoding="utf-8")
    (workspace / "external").symlink_to(outside, target_is_directory=True)
    for mode in ("build", "edit", "yolo"):
        decision = check_permission(mode, {}, "glob", {"pattern": "external/*.md"})
        assert decision["rule_id"] == "hard.deny"
    output = tool_glob("external/*.md")
    assert output.startswith("Error:") and "private.md" not in output


@pytest.mark.parametrize("directory", [False, True])
def test_glob_wildcard_outside_symlink_never_returns_external_name(tmp_data_dir, workspace, directory):
    """固定前缀之后才遇到外链时，再验结果真实路径；错误也不能暴露外部文件名。"""
    outside = tmp_data_dir / "outside"
    outside.mkdir()
    secret = outside / "private.md"
    secret.write_text("private", encoding="utf-8")
    if directory:
        (workspace / "external").symlink_to(outside, target_is_directory=True)
        pattern = "ext*/*.md"
    else:
        (workspace / "external.md").symlink_to(secret)
        pattern = "*.md"
    output = tool_glob(pattern)
    assert output.startswith("Error:")
    assert "private.md" not in output and "external.md" not in output


def test_glob_inside_symlink_still_works(workspace):
    """工作区内链接不误拒，返回调用者看到的相对别名。"""
    (workspace / "docs").mkdir()
    (workspace / "docs" / "a.md").write_text("safe", encoding="utf-8")
    (workspace / "alias").symlink_to(workspace / "docs", target_is_directory=True)
    assert check_permission("build", {}, "glob", {"pattern": "alias/*.md"})["decision"] == "allow"
    assert tool_glob("alias/*.md") == "alias/a.md"


@pytest.mark.parametrize("returncode", [1, -9])
def test_nonzero_bash_prefix_survives_output_truncation(monkeypatch, returncode):
    """只模拟失败进程；长输出截断后仍留错误前缀、尾部原因和退出码。"""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a, returncode, "x" * 40000, "\nfailed at tail"))
    output = tool_bash("echo simulated")
    assert output.startswith("Error:")
    assert "failed at tail" in output and f"[exit code: {returncode}]" in output
    assert len(output) < 30200


@pytest.mark.parametrize("returncode,status", [(0, "completed"), (1, "failed")])
def test_command_exit_status_reaches_events_replay_and_model(store_db, monkeypatch, returncode, status):
    """真实循环/记录器配假 LLM 和假进程：失败可回放且喂回模型，成功路径不变。"""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a, returncode, "plain output", ""))

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def stream(self, **kwargs):
            self.calls.append(kwargs["messages"][:])
            if len(self.calls) == 1:
                yield {"type": "tool_use", "id": "command", "name": "bash", "input": {"command": "echo simulated"}}
            else:
                yield {"type": "text_delta", "text": "已检查命令结果"}

    fake = FakeClient()
    recorder = TurnRecorder(store_db, "command-review", model="fake", max_tokens=1000)
    deps = TurnDeps(client=fake, recorder=recorder, system_prompt="fake",
                    history=[{"role": "user", "content": "检查命令"}])

    async def collect():
        return [event async for event in run_turn(deps)]

    events = asyncio.run(collect())
    completed = next(event for event in events if event["type"] == "tool_completed")
    assert completed["status"] == status
    turn = replay.load_replay(store_db, "command-review")["turns"][0]
    card = next(item for item in turn["work_items"] if item["kind"] == "tool_call")
    assert card["status"] == status
    model_result = fake.calls[1][-1]["content"][0]
    assert model_result["content"].startswith("Error:") is (returncode != 0)
    history = replay.load_history(store_db, "command-review")
    persisted_result = next(block for message in history if isinstance(message["content"], list)
                            for block in message["content"] if block["type"] == "tool_result")
    assert bool(persisted_result.get("is_error")) is (returncode != 0)
    assert events[-1]["state"] == "success"
