"""任务板：mini_harness §6 todo_write 的移植。

- 全量替换语义：每次调用用新列表覆盖任务板；
- 校验：≤20 条、status 三态、进行中最多 1 条；
- Web 版差异：合法更新会落一张 todo part（覆盖显示最新状态）并推
  todo_updated 事件；非法输入返回 Error 字符串（不落盘）。
"""

from app.models import new_id

VALID_STATUS = ("pending", "in_progress", "completed")
MAX_ITEMS = 20

_STATUS_MARK = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}


def parse_items(raw) -> tuple[list[dict] | None, str]:
    """解析并校验任务列表。返回 (items|None, 错误信息)。"""
    if isinstance(raw, str):
        # 容错：模型可能传 JSON 字符串
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            return None, "Error: items 不是合法 JSON"

    if not isinstance(raw, list):
        return None, "Error: items 必须是数组"
    if len(raw) > MAX_ITEMS:
        return None, f"Error: 任务数超过上限 {MAX_ITEMS}"

    items: list[dict] = []
    in_progress = 0
    for i, entry in enumerate(raw):
        if isinstance(entry, str):
            entry = {"content": entry}
        if not isinstance(entry, dict):
            return None, f"Error: 第 {i + 1} 条任务格式不正确"
        content = str(entry.get("content", "")).strip()
        status = entry.get("status", "pending")
        if not content:
            return None, f"Error: 第 {i + 1} 条任务内容为空"
        if status not in VALID_STATUS:
            return None, f"Error: 第 {i + 1} 条任务状态非法：{status}"
        if status == "in_progress":
            in_progress += 1
        items.append({"content": content, "status": status})
    if in_progress > 1:
        return None, "Error: 进行中的任务最多 1 条"
    return items, ""


def render_panel(items: list[dict]) -> str:
    """渲染任务板文本（[ ] 待办 / [>] 进行中 / [x] 已完成）。"""
    if not items:
        return "(任务板为空)"
    lines = [f"{_STATUS_MARK[it['status']]} {it['content']}" for it in items]
    return "\n".join(lines)


async def handle_todo_write(deps, message_id: str, tool_input: dict) -> tuple[str, list[dict]]:
    """loop 专用入口：校验 → 落盘 → 返回事件（由循环统一转发，保证事件顺序）。

    返回 (输出文本, 附加事件)。与 subtask 不同：这里没有长等待，
    不需要直推队列，走循环的标准事件通道即可。
    """
    items, error = parse_items(tool_input.get("items"))
    if items is None:
        return error, []

    deps.recorder.write_todo_part(message_id, items)
    return render_panel(items), [{"type": "todo_updated", "items": items}]
