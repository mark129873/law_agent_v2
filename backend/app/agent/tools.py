"""工具层：TOOLS（给模型的 schema 列表）+ TOOL_HANDLERS（名字→函数）双表。

移植自 mini_harness §2/§5：
- 加工具只改这两处；
- 所有文件工具经 safe_path 限制在 data/workspace/ 沙箱内（产品决策：固定工作区）；
- 工具出错一律返回 "Error: ..." 字符串，绝不抛异常打断主循环（mini_harness §2 哲学）；
- bash 用 PowerShell 执行（产品决策），输出保留尾部 3 万字符（报错通常在末尾）；
- delete_file 移入 workspace 内的 .rubbish/（可找回；rubbish 放沙箱内，
  因为存储层已无顶层 rubbish 目录——见 ARCHITECTURE §2.4）。
"""

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from app.config import settings

# ---------- 沙箱 ----------


def workspace_root() -> Path:
    """工具沙箱根目录（data/workspace/），启动时已建好。"""
    return settings.data_dir / "workspace"


def safe_path(relative: str) -> Path:
    """把相对路径解析为沙箱内的绝对路径；越界直接拒绝。

    为什么这么严：所有文件工具都过这一道，杜绝 "..\\" 或绝对路径逃出
    workspace（mini_harness §5 safe_path 的同款防御）。
    """
    root = workspace_root().resolve()
    target = (root / relative).resolve()
    if not (target == root or target.is_relative_to(root)):
        raise ValueError(f"路径越界：{relative} 必须位于工作区内")
    return target


# ---------- 工具实现（全部同步函数，主循环用线程池调） ----------

# 输出上限：与 mini_harness 一致，bash 输出保留尾部（报错通常在末尾）
_OUTPUT_LIMIT = 30000
_READ_LIMIT = 60000


def _tail(text: str, limit: int = _OUTPUT_LIMIT) -> str:
    """保留尾部 limit 字符。"""
    return text if len(text) <= limit else f"...(前段已截断)...\n{text[-limit:]}"


def tool_bash(command: str) -> str:
    """在 workspace 内用 PowerShell 执行命令，返回 stdout+stderr 合并输出。"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=workspace_root(),
            capture_output=True,
            text=True,
            timeout=settings.bash_timeout,
            encoding="utf-8",
            errors="replace",
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        exit_note = "" if completed.returncode == 0 else f"\n[exit code: {completed.returncode}]"
        return _tail(output.rstrip()) + exit_note
    except subprocess.TimeoutExpired:
        return f"Error: 命令超时（>{settings.bash_timeout}s）"
    except Exception as exc:  # 兜底：任何执行环境问题都转字符串喂回模型
        return f"Error: 命令执行失败：{exc}"


def tool_read_file(path: str) -> str:
    """读文本文件，超长截断。"""
    try:
        target = safe_path(path)
        if not target.is_file():
            return f"Error: 文件不存在：{path}"
        text = target.read_text(encoding="utf-8", errors="replace")
        return _tail(text, _READ_LIMIT)
    except Exception as exc:
        return f"Error: 读取失败：{exc}"


def tool_write_file(path: str, content: str) -> str:
    """写文本文件（覆盖式），自动建父目录。"""
    try:
        target = safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字符）"
    except Exception as exc:
        return f"Error: 写入失败：{exc}"


def tool_edit_file(path: str, old_text: str, new_text: str) -> str:
    """精确替换：old_text 必须在文件中恰好出现一次（mini_harness 规则，防误改）。"""
    try:
        target = safe_path(path)
        if not target.is_file():
            return f"Error: 文件不存在：{path}"
        text = target.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_text)
        if count == 0:
            return "Error: old_text 在文件中未找到"
        if count > 1:
            return f"Error: old_text 出现了 {count} 次，要求恰好 1 次；请提供更长的片段"
        target.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"已编辑 {path}"
    except Exception as exc:
        return f"Error: 编辑失败：{exc}"


def tool_glob(pattern: str) -> str:
    """在沙箱内按 glob 模式列文件（相对路径，每行一个）。

    输出统一用正斜杠：模型更常写 / 风格路径，Windows 的 \\ 会误导它。
    """
    try:
        root = workspace_root()
        matches = sorted(
            p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file()
        )
        if not matches:
            return "(无匹配文件)"
        return _tail("\n".join(matches), _READ_LIMIT)
    except Exception as exc:
        return f"Error: glob 失败：{exc}"


def tool_delete_file(path: str) -> str:
    """删除文件 = 移入沙箱内 .rubbish/<时间戳>_<名>/（可人工找回）。"""
    try:
        target = safe_path(path)
        if not target.is_file():
            return f"Error: 文件不存在：{path}"
        import time

        trash_name = f"{int(time.time() * 1000)}_{target.name}"
        trash_dir = workspace_root() / ".rubbish"
        trash_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(trash_dir / trash_name))
        return f"已删除 {path}（移入 .rubbish/{trash_name}，可找回）"
    except Exception as exc:
        return f"Error: 删除失败：{exc}"


def tool_load_skill(name: str) -> str:
    """读取技能全文（实现见 skills.py；这里挂进分发表）。"""
    from app.agent.skills import tool_load_skill as _impl

    return _impl(name)


# ---------- 双表注册 ----------

TOOL_HANDLERS: dict[str, Callable[..., str]] = {
    "bash": tool_bash,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "glob": tool_glob,
    "delete_file": tool_delete_file,
    "load_skill": tool_load_skill,
}


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    """构造 Anthropic 工具 schema（mini_harness 同款形态）。"""
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required},
    }


TOOLS: list[dict] = [
    _schema(
        "bash", "在工作区内用 PowerShell 执行命令，返回合并输出。工作目录即工作区根。",
        {"command": {"type": "string", "description": "PowerShell 命令"}}, ["command"],
    ),
    _schema(
        "read_file", "读取工作区内文本文件（超长截断尾部保留）。",
        {"path": {"type": "string", "description": "工作区内相对路径"}}, ["path"],
    ),
    _schema(
        "write_file", "写入/覆盖工作区内文本文件，自动创建父目录。",
        {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"],
    ),
    _schema(
        "edit_file", "精确替换文件片段；old_text 必须在文件中恰好出现一次。",
        {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        ["path", "old_text", "new_text"],
    ),
    _schema(
        "glob", "按 glob 模式列出工作区文件（如 **/*.py）。",
        {"pattern": {"type": "string"}}, ["pattern"],
    ),
    _schema(
        "delete_file", "删除工作区文件（移入 .rubbish/ 可找回）。",
        {"path": {"type": "string"}}, ["path"],
    ),
    _schema(
        "todo_write", "全量更新任务板（≤20 条；进行中最多 1 条）。",
        {
            "items": {
                "type": "array",
                "description": "任务数组，每项 {content: str, status: pending|in_progress|completed}",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    },
                    "required": ["content", "status"],
                },
            }
        },
        ["items"],
    ),
    _schema(
        "load_skill", "加载指定技能的完整说明文档（按需加载，先看目录再取）。",
        {"name": {"type": "string", "description": "技能目录名"}}, ["name"],
    ),
    _schema(
        "subtask", "派出子助手独立完成一个目标（有自己的工具循环），返回其最终汇报。",
        {"goal": {"type": "string", "description": "子助手要完成的单一目标，要写得具体"}}, ["goal"],
    ),
]


def execute_tool(name: str, tool_input: dict) -> str:
    """统一执行入口：查表分发 + 异常兜底为 Error 字符串（mini_harness §2）。"""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"Error: 未知工具 {name}"
    try:
        return handler(**tool_input)
    except TypeError as exc:
        return f"Error: 参数不匹配：{exc}"
    except Exception as exc:
        return f"Error: {exc}"
