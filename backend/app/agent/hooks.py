"""hooks 事件点：mini_harness §4 的四事件点移植。

- 事件点：UserPromptSubmit / PreToolUse / PostToolUse / Stop；
- 第一个返回非 None 的回调短路生效（Stop hook 返回字符串可注入续轮内容）；
- 与 mini_harness 的差异：权限检查不放 hook（它需要异步审批回调，
  由主循环直接消费 permissions.check，见 loop.py），hooks 只承担
  日志与输出整形等纯同步副作用。
"""

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 事件点 -> 回调列表
_registry: dict[str, list[Callable[[dict], Any]]] = {
    "UserPromptSubmit": [],
    "PreToolUse": [],
    "PostToolUse": [],
    "Stop": [],
}


def register(event: str, callback: Callable[[dict], Any]) -> None:
    """注册回调。事件名必须是四事件点之一。"""
    if event not in _registry:
        raise ValueError(f"未知 hook 事件：{event}")
    _registry[event].append(callback)


def trigger(event: str, context: dict) -> Any:
    """依次触发回调，第一个非 None 返回值短路并返回它（mini_harness 语义）。"""
    for callback in _registry.get(event, []):
        result = callback(context)
        if result is not None:
            return result
    return None


def _log_pre_tool(context: dict) -> None:
    """工程事件日志：工具调用开始（只记名字与 id，不记参数全文）。"""
    logger.info("工具调用开始 tool=%s call_id=%s", context.get("name"), context.get("tool_call_id"))


def _log_post_tool(context: dict) -> None:
    """工程事件日志：工具调用结束（记状态与耗时）。"""
    logger.info(
        "工具调用结束 tool=%s call_id=%s status=%s 耗时=%.0fms",
        context.get("name"),
        context.get("tool_call_id"),
        context.get("status"),
        context.get("duration_ms", 0) or 0,
    )


def register_default_hooks() -> None:
    """挂载默认 hook 集（等价 mini_harness 的 log_hook / large_output_hook）。"""
    register("PreToolUse", _log_pre_tool)
    register("PostToolUse", _log_post_tool)
