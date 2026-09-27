"""BE-8 验证：todo_write / load_skill / subtask（假 LLM 驱动嵌套循环）。"""

import asyncio
import json

import pytest

from app.agent import skills
from app.agent.loop import TurnDeps, run_turn
from app.agent.todo import handle_todo_write, parse_items, render_panel
from app.agent.tools import execute_tool, workspace_root
from app.sessions import replay
from app.sessions.recorder import TurnRecorder


class ScriptClient:
    """按调用次序出脚本的假流式客户端（主循环与子循环共用一个客户端）。"""

    def __init__(self, scripts: list[list[dict]]) -> None:
        self.scripts = list(scripts)

    async def stream(self, *, system, messages, tools):
        for event in self.scripts.pop(0):
            yield event


@pytest.fixture()
def _ws(tmp_data_dir):
    workspace_root().mkdir(parents=True, exist_ok=True)


@pytest.fixture()
def recorder(tmp_data_dir, _ws):
    from app import db

    db.init_db(tmp_data_dir)
    session = db.new_session()
    rec = TurnRecorder(session, "s1", model="test-model", max_tokens=1000)
    yield rec
    session.close()


def _run(deps) -> list[dict]:
    """镜像真实端点的收集方式：run_turn 事件泵进队列，subtask 等直推事件也进同一队列。"""

    async def collect():
        queue: asyncio.Queue = asyncio.Queue()
        deps.queue = queue

        async def pump():
            async for e in run_turn(deps):
                await queue.put(e)
            await queue.put(None)

        task = asyncio.create_task(pump())
        events: list[dict] = []
        while True:
            e = await queue.get()
            if e is None:
                break
            events.append(e)
        await task
        return events

    return asyncio.run(collect())


# ---------- todo_write ----------


def test_todo_parse_validation() -> None:
    """校验：数量上限、状态非法、进行中最多 1 条、JSON 字符串容错。"""
    items, err = parse_items([{"content": "a", "status": "pending"}])
    assert err == "" and items[0]["content"] == "a"

    _, err = parse_items([{"content": str(i), "status": "pending"} for i in range(21)])
    assert "上限" in err

    _, err = parse_items([{"content": "a", "status": "doing"}])
    assert "非法" in err

    _, err = parse_items([
        {"content": "a", "status": "in_progress"},
        {"content": "b", "status": "in_progress"},
    ])
    assert "最多 1 条" in err

    items, err = parse_items('[{"content": "json 串", "status": "pending"}]')
    assert err == "" and items[0]["content"] == "json 串"


def test_todo_render_panel() -> None:
    """面板渲染：[ ]/[>]/[x] 三态。"""
    panel = render_panel([
        {"content": "已完成的事", "status": "completed"},
        {"content": "正在做的事", "status": "in_progress"},
        {"content": "待办的事", "status": "pending"},
    ])
    lines = panel.splitlines()
    assert lines[0].startswith("[x]") and lines[1].startswith("[>]") and lines[2].startswith("[ ]")


def test_todo_write_via_loop(recorder) -> None:
    """todo_write 走主循环：落 todo part + todo_updated 事件 + 工具结果为面板文本。"""
    client = ScriptClient([
        [{"type": "tool_use", "id": "td1", "name": "todo_write",
          "input": {"items": [{"content": "写代码", "status": "in_progress"},
                               {"content": "测试", "status": "pending"}]}}],
        [{"type": "text_delta", "text": "任务板已更新"}],
    ])
    queue: asyncio.Queue = asyncio.Queue()
    deps = TurnDeps(client=client, recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "做个计划"}], queue=queue)
    events = _run(deps)

    assert any(e["type"] == "todo_updated" and len(e["items"]) == 2 for e in events)
    done = next(e for e in events if e["type"] == "tool_completed")
    assert done["status"] == "completed"
    assert "[>]" in done["output_preview"] and "[ ]" in done["output_preview"]

    # 队列里也有 todo_updated（真实 SSE 场景由泵转发，这里直推）
    # 回放：todo 卡在块内
    result = replay.load_replay(deps.recorder.db, "s1")
    todo_items = [x for x in result["turns"][0]["work_items"] if x["kind"] == "todo"]
    assert len(todo_items) == 1 and todo_items[0]["items"][1]["status"] == "pending"


def test_todo_invalid_input_no_persist(recorder) -> None:
    """非法 todo 输入：Error 结果、状态 failed、不落盘。"""
    client = ScriptClient([
        [{"type": "tool_use", "id": "td2", "name": "todo_write",
          "input": {"items": [{"content": "a", "status": "in_progress"},
                               {"content": "b", "status": "in_progress"}]}}],
        [{"type": "text_delta", "text": "知道了"}],
    ])
    deps = TurnDeps(client=client, recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "计划"}])
    events = _run(deps)
    done = next(e for e in events if e["type"] == "tool_completed")
    assert done["status"] == "failed"
    result = replay.load_replay(deps.recorder.db, "s1")
    assert all(x["kind"] != "todo" for x in result["turns"][0]["work_items"])


# ---------- load_skill ----------


def test_load_skill(tmp_path, monkeypatch) -> None:
    """技能加载：正常读取 / 不存在 / 非法名。"""
    skill_dir = tmp_path / "code-review"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# 代码审查技能\n检查清单...", encoding="utf-8")
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path)

    result = execute_tool("load_skill", {"name": "code-review"})
    assert "代码审查技能" in result

    assert execute_tool("load_skill", {"name": "nope"}).startswith("Error:")
    assert execute_tool("load_skill", {"name": "../etc"}).startswith("Error:")
    assert skills.list_skill_names() == ["code-review"]


# ---------- subtask ----------


def test_subtask_full_flow(recorder) -> None:
    """subtask 嵌套循环：子助手用基础工具干活 → 实时事件 → 结果回父级。"""
    client = ScriptClient([
        # 主循环第 1 步：派子助手
        [{"type": "tool_use", "id": "st1", "name": "subtask",
          "input": {"goal": "在工作区写 notes.txt 内容为 sub-ok"}}],
        # 子循环第 1 步：写文件
        [{"type": "tool_use", "id": "c1", "name": "write_file",
          "input": {"path": "notes.txt", "content": "sub-ok"}}],
        # 子循环第 2 步：汇报（无工具 → 子循环结束）
        [{"type": "text_delta", "text": "已完成：notes.txt 写入 sub-ok"}],
        # 主循环第 2 步：收口
        [{"type": "text_delta", "text": "子任务完成"}],
    ])
    queue: asyncio.Queue = asyncio.Queue()
    deps = TurnDeps(client=client, recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "派个子任务"}], queue=queue)
    events = _run(deps)

    kinds = [e["type"] for e in events]
    assert "subtask_started" in kinds and "subtask_completed" in kinds
    # 子循环的文本实时推进事件
    deltas = [e["text"] for e in events if e["type"] == "subtask_delta"]
    assert any("已完成" in d for d in deltas)
    # 主循环的工具结果 = 子助手最终汇报
    done = next(e for e in events if e["type"] == "tool_completed" and e["name"] == "subtask")
    assert "notes.txt 写入 sub-ok" in done["output_preview"]
    assert events[-1]["state"] == "success"

    # 文件真的被子助手写进沙箱
    assert (workspace_root() / "notes.txt").read_text(encoding="utf-8") == "sub-ok"

    # 回放：subtask 卡在块内（goal + 最终输出）
    result = replay.load_replay(deps.recorder.db, "s1")
    subs = [x for x in result["turns"][0]["work_items"] if x["kind"] == "subtask"]
    assert len(subs) == 1
    assert subs[0]["goal"] == "在工作区写 notes.txt 内容为 sub-ok"
    assert "notes.txt 写入 sub-ok" in subs[0]["output"]
    assert subs[0]["status"] == "completed"


def test_subtask_missing_goal(recorder) -> None:
    """缺 goal：Error 结果，failed 状态。"""
    client = ScriptClient([
        [{"type": "tool_use", "id": "st2", "name": "subtask", "input": {}}],
        [{"type": "text_delta", "text": "收到"}],
    ])
    deps = TurnDeps(client=client, recorder=recorder, system_prompt="测试",
                    history=[{"role": "user", "content": "派"}])
    events = _run(deps)
    done = next(e for e in events if e["type"] == "tool_completed")
    assert done["status"] == "failed"
