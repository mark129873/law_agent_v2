"""harness.py 的离线测试(不调用 API,不需要 API key).

验证 harness 的安全关键约束: 路径沙箱,删除->rubbish,
Unix/PowerShell 两套拦截规则,todo_write 校验与任务面板渲染,
技能解析,压缩管线,goal 判断器 JSON 校验,工具 schema 一致性.

这些逻辑决定了"强制约束"是否真的强制 -- 改动 harness
后跑一遍测试,比肉眼审查可靠; 测试放在 harness.py 外部,保持单文件
本身只含运行所需代码.

运行方式:
    uv run test/test_harness.py           # 按下方脚本元数据自动装依赖
    uv run pytest test/                   # 项目环境: 需先 uv sync
"""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "anthropic",
#     "python-dotenv",
#     "pyyaml",
#     "pytest",
# ]
# ///

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

# 让测试能 import 到上层的 harness.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False


@contextmanager
def sandbox():
    """把 harness 的项目根目录全局变量重定向到临时目录,结束后还原.
    safe_path / run_delete_file 等函数都在运行时读模块级全局变量,
    重定向即可让测试在隔离沙箱里跑,不污染真实项目."""
    tmp = Path(tempfile.mkdtemp(prefix="harness_test_"))
    saved = (harness.PROJECT_ROOT, harness.RUBBISH_DIR,
             harness.TRANSCRIPT_DIR, harness.TOOL_RESULTS_DIR)
    harness.PROJECT_ROOT = tmp
    harness.RUBBISH_DIR = tmp / "rubbish"
    harness.TRANSCRIPT_DIR = tmp / ".transcripts"
    harness.TOOL_RESULTS_DIR = tmp / ".task_outputs" / "tool-results"
    try:
        yield tmp
    finally:
        (harness.PROJECT_ROOT, harness.RUBBISH_DIR,
         harness.TRANSCRIPT_DIR, harness.TOOL_RESULTS_DIR) = saved
        shutil.rmtree(tmp, ignore_errors=True)


# ---- 沙箱 ----

def test_safe_path_keeps_paths_inside_project():
    (harness.PROJECT_ROOT / "a.txt").write_text("x", encoding="utf-8")
    assert harness.safe_path("a.txt") == (harness.PROJECT_ROOT / "a.txt").resolve()
    for bad in ("../x", "/etc/passwd",
                "C:\\Windows\\x" if harness.IS_WINDOWS else "~/x"):
        try:
            harness.safe_path(bad)
        except ValueError:
            continue
        raise AssertionError(f"safe_path accepted {bad}")


def test_ts_millis_has_17_digits():
    assert re.fullmatch(r"\d{17}", harness.ts_millis())


# ---- 删除 -> rubbish ----

def test_delete_file_moves_to_rubbish():
    (harness.PROJECT_ROOT / "doomed.txt").write_text("bye", encoding="utf-8")
    out = harness.run_delete_file("doomed.txt")
    assert out.startswith("Moved"), out
    assert not (harness.PROJECT_ROOT / "doomed.txt").exists()
    assert any(harness.RUBBISH_DIR.iterdir()), "rubbish is empty"
    # 保护对象: rubbish 自身,项目根,不存在的路径
    assert harness.run_delete_file("rubbish").startswith("Error")
    assert harness.run_delete_file("no_such.txt").startswith("Error")


# ---- 权限拦截(两套平台规则都要验证)----

def test_delete_rules_unix():
    assert harness.match_delete_command("rm a.txt", windows=False)
    assert harness.match_delete_command("echo ok; rm -rf x", windows=False)
    assert harness.match_delete_command("find . -name x -delete", windows=False)
    assert not harness.match_delete_command("echo remove", windows=False)
    assert not harness.match_delete_command("model rm", windows=False)


def test_delete_rules_windows():
    assert harness.match_delete_command("Remove-Item a.txt", windows=True)
    assert harness.match_delete_command("del test.txt", windows=True)
    assert harness.match_delete_command("rm a.txt", windows=True)
    assert not harness.match_delete_command("Get-Item a.txt", windows=True)
    assert not harness.match_delete_command("echo Remove-Item", windows=True)


def test_outside_write_rules_unix():
    assert harness.match_outside_write("echo hi > /etc/passwd", windows=False)
    assert harness.match_outside_write("cp a /tmp/", windows=False)
    assert harness.match_outside_write("cd /tmp", windows=False)
    assert not harness.match_outside_write("echo hi > /dev/null", windows=False)
    assert not harness.match_outside_write("echo hi > out.txt", windows=False)
    assert not harness.match_outside_write("cd src", windows=False)


def test_outside_write_rules_windows():
    assert harness.match_outside_write("dir > C:\\Windows\\x", windows=True)
    assert harness.match_outside_write("cd ..", windows=True)
    assert not harness.match_outside_write("dir > out.txt", windows=True)
    assert not harness.match_outside_write("cd src", windows=True)


def test_permission_hook_gate():
    # "rm data.csv" 在两套平台词表里都命中命令位置的删除规则
    block = SimpleNamespace(name="bash", input={"command": "rm data.csv"})
    assert harness.permission_hook(block) is not None
    # delete_file 是唯一合法的删除通道
    del_block = SimpleNamespace(name="delete_file", input={"path": "x"})
    assert harness.permission_hook(del_block) is None
    # 文件工具路径越界: 硬拒
    esc_block = SimpleNamespace(name="write_file", input={"path": "../evil.txt"})
    assert harness.permission_hook(esc_block) is not None


# ---- todo_write: TodoManager 内存任务板 ----

def test_todo_manager_update_and_render():
    # 正常写入: 渲染成 [ ]/[>]/[x] 任务面板
    out = harness.run_todo_write([{"content": "step one", "status": "pending"},
                                  {"content": "step two", "status": "in_progress"}])
    assert "[ ] step one" in out and "[>] step two" in out
    assert "(0/2 completed)" in out
    # 全量替换语义: 再写一次只剩新内容
    out = harness.run_todo_write([{"content": "only", "status": "completed"}])
    assert "(1/1 completed)" in out and "step one" not in out
    # 约束: 同时只允许一个 in_progress
    bad = [{"content": "a", "status": "in_progress"},
           {"content": "b", "status": "in_progress"}]
    assert harness.run_todo_write(bad).startswith("Error")
    # 约束: 空内容/非法状态/超 20 条
    assert harness.run_todo_write([{"content": "", "status": "pending"}]).startswith("Error")
    assert harness.run_todo_write([{"content": "x", "status": "done"}]).startswith("Error")
    assert harness.run_todo_write([{"content": "x", "status": "pending"}] * 21).startswith("Error")
    # 字符串形式的列表也能解析(s05 的两级兜底: json 失败 -> ast.literal_eval)
    out = harness.run_todo_write('[{"content": "json todo", "status": "pending"}]')
    assert "[ ] json todo" in out
    out = harness.run_todo_write("[{'content': 'repr todo', 'status': 'pending'}]")
    assert "[ ] repr todo" in out


# ---- Skill Loading ----

def test_skill_loader_scan_and_load():
    skill_dir = harness.PROJECT_ROOT / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: A demo skill for checks.\n---\n\n# Demo\nbody",
        encoding="utf-8")
    loader = harness.SkillLoader(harness.PROJECT_ROOT / "skills")
    assert "demo" in loader.skills
    assert "A demo skill for checks." in loader.skills["demo"]["description"]
    assert "body" in loader.load("demo")
    assert loader.load("nope").startswith("Error")


# ---- 压缩管线 ----

def test_compactor_snip_and_budget():
    compactor = harness.ContextCompactor(
        None, harness.MODEL, harness.TRANSCRIPT_DIR, harness.TOOL_RESULTS_DIR)
    # snip: 消息数超限时归档中间历史
    messages = [{"role": "user", "content": f"message {i}"} for i in range(60)]
    snipped = compactor.snip_compact(list(messages))
    assert len(snipped) < 60, "snip_compact did not shrink"
    assert any(compactor.is_archive_marker(m) for m in snipped)
    # budget: 最新一批超大 tool_result 落盘留预览
    batch = [{"role": "user", "content": [{"type": "tool_result",
              "tool_use_id": "t1", "content": "x" * 40000}]}]
    budgeted = compactor.tool_result_budget(batch, max_chars=1000)
    content = budgeted[-1]["content"][0]["content"]
    assert content.startswith("<persisted-output>"), content[:50]


def test_compactor_token_estimation():
    compactor = harness.ContextCompactor(
        None, harness.MODEL, harness.TRANSCRIPT_DIR, harness.TOOL_RESULTS_DIR)
    # 首轮无基线 -> 字符兜底估算
    assert compactor.estimate_tokens([{"role": "user", "content": "hello"}]) > 0
    # 有真实计量后 -> 基线 + 字符增量换算
    compactor.last_input_tokens = 1000
    compactor.chars_at_measure = compactor.estimate_chars(
        [{"role": "user", "content": "hello"}])
    bigger = [{"role": "user", "content": "hello" * 400}]
    assert compactor.estimate_tokens(bigger) >= 1000


# ---- goal 判断器 JSON 校验 ----

def test_goal_parse_json_object():
    assert harness._parse_json_object('{"ok": true, "reason": "done"}')["ok"] is True
    fenced = '```json\n{"ok": false, "reason": "not yet", "impossible": true}\n```'
    assert harness._parse_json_object(fenced)["impossible"] is True
    for bad in ('{"ok": "yes", "reason": "x"}', "not json",
                '{"ok": true, "reason": "x", "impossible": true}'):
        try:
            harness._parse_json_object(bad)
        except harness.GoalError:
            continue
        raise AssertionError(f"accepted invalid: {bad}")


# ---- 工具 schema 与 dispatch 一致性 ----

def test_tools_and_handlers_consistent():
    names = [tool["name"] for tool in harness.TOOLS]
    assert len(names) == len(set(names)), "duplicate tool name"
    assert set(names) == set(harness.TOOL_HANDLERS), "TOOLS 与 TOOL_HANDLERS 不一致"
    assert {"subtask", "todo_write", "load_skill", "delete_file"} <= set(names)
    # 子 agent 工具池不含 subtask(防递归),也不含计划/技能工具
    sub_names = {tool["name"] for tool in harness.SUB_TOOLS}
    assert "subtask" not in sub_names and "todo_write" not in sub_names


# ---- 运行入口 ----

if HAS_PYTEST:
    @pytest.fixture(autouse=True)
    def _auto_sandbox():
        with sandbox():
            yield


if __name__ == "__main__":
    # 无 pytest 时的兜底运行方式: 同一个沙箱里顺序跑完全部测试
    failures = 0
    with sandbox():
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                    print(f"  PASS  {name}")
                except Exception as e:
                    failures += 1
                    print(f"  FAIL  {name}: {e}")
    print(f"\n{'ALL TESTS PASSED' if not failures else f'{failures} FAILURE(S)'}")
    sys.exit(1 if failures else 0)
