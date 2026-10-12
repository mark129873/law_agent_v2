"""会话工具授权：只存进程内存，精确范围，不读取或写入项目规则。"""

import json
import os

from app.agent.tools import safe_path

# 文件内容可以在已批准的目标内变化；未知字段必须参与精确匹配，不能扩权。
_FILE_PAYLOADS = {
    "read_file": (), "write_file": ("content",),
    "edit_file": ("old_text", "new_text"), "delete_file": (),
}
_grants: dict[str, set[tuple[str, str]]] = {}


def scope_for(tool: str, tool_input: dict) -> tuple[str, str] | None:
    """文件绑定规范路径；命令/其他工具绑定完整参数，不解释通配或命令前缀。"""
    scoped = dict(tool_input)
    if tool in _FILE_PAYLOADS:
        path = scoped.get("path")
        if not isinstance(path, str) or not path:
            return None
        try:
            scoped["path"] = os.path.normcase(str(safe_path(path)))
        except (ValueError, OSError):
            return None
        for key in _FILE_PAYLOADS[tool]:
            scoped.pop(key, None)
    try:
        # 完整JSON包括嵌套参数、未知字段；不做strip/lower，保留命令原义。
        return tool, json.dumps(scoped, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None


def describe(scope: tuple[str, str] | None) -> str:
    """弹窗直接展示可复用范围，避免用户把本会话允许误解为整个工具放行。"""
    if scope is None:
        return "参数无效，无法保存会话授权"
    tool, content = scope
    prefix = "目标" if tool in _FILE_PAYLOADS else "完整命令参数（精确匹配）" if tool == "bash" else "完整参数（精确匹配）"
    return f"{prefix}：{content}"


def grant(session_id: str, scope: tuple[str, str]) -> None:
    _grants.setdefault(session_id, set()).add(scope)


def allows(session_id: str, tool: str, tool_input: dict) -> bool:
    scope = scope_for(tool, tool_input)
    return scope is not None and scope in _grants.get(session_id, set())


def forget_session(session_id: str) -> None:
    _grants.pop(session_id, None)
