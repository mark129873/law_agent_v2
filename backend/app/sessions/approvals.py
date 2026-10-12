"""交互审批：绑定会话的进程内等待槽，三选项决定，不修改项目权限配置。"""

import asyncio
from copy import deepcopy
import logging

from app.agent.permission_service import evaluate
from app.models import new_id, now_ms
from app.sessions import session_permissions

logger = logging.getLogger(__name__)

# 槽位与审批事实均只在进程内；刷新可恢复，重启即失效。
_pending: dict[str, dict] = {}
_events: dict[str, dict[str, dict]] = {}


def approval_events(session_id):
    return list(_events.get(session_id, {}).values())


def forget_turn(session_id, turn_id):
    """重跑只清该轮审批；会话授权跨turn保留。"""
    records = _events.get(session_id, {})
    for key in [k for k, v in records.items() if v['turn_id'] == turn_id]:
        records.pop(key, None)
        slot = _pending.pop(key, None)
        if slot:
            slot["event"].set()


def forget_session(session_id: str) -> None:
    """删除会话释放全部运行内存，不把旧授权留给同ID的后续会话。"""
    _events.pop(session_id, None)
    for key, slot in list(_pending.items()):
        if slot["session_id"] == session_id:
            _pending.pop(key, None)
            slot["event"].set()
    session_permissions.forget_session(session_id)


def _options_for_scope(scope: tuple[str, str] | None) -> list[dict]:
    """选项文案与授权保存使用同一范围快照，不重复解析可能变化的符号链接。"""
    return [
        {"option_id": "allowOnce", "label": "仅本次允许"},
        {"option_id": "allowSession", "label": "本会话内始终允许",
         "content": session_permissions.describe(scope)},
        {"option_id": "deny", "label": "拒绝"},
    ]


def build_options(tool_name: str, tool_input: dict) -> list[dict]:
    """主助手、子助手与刷新回放共用相同三项，并展示实际授权范围。"""
    return _options_for_scope(session_permissions.scope_for(tool_name, tool_input))


def register(request_id: str, tool: str = "", input_data: dict | None = None, *, session_id: str,
             stop_event: asyncio.Event | None = None) -> dict:
    """绑定所属会话和参数快照；后续模型参数变化不能改变已展示的范围。"""
    snapshot = deepcopy(input_data or {})
    scope = session_permissions.scope_for(tool, snapshot)
    slot = {
        "event": asyncio.Event(), "approved": False, "option_id": None, "feedback": None,
        "session_id": session_id, "tool": tool, "input": snapshot,
        "scope": scope,
        "options": _options_for_scope(scope),
        "stop_event": stop_event,
    }
    _pending[request_id] = slot
    return slot


def get_request(request_id: str) -> dict | None:
    """已停止的请求立即作废；不能先写权限、再发现批准已经迟到。"""
    slot = _pending.get(request_id)
    if slot is not None and slot["stop_event"] is not None and slot["stop_event"].is_set():
        _pending.pop(request_id, None)
        slot["event"].set()
        return None
    return slot


def resolve(request_id: str, option_id: str, *, session_id: str,
            feedback: str | None = None) -> bool:
    """只取出一次；错误会话、未知选项与重放均不改决定或授权。"""
    slot = get_request(request_id)
    if slot is None or slot["session_id"] != session_id:
        return False
    if option_id not in {"allowOnce", "allowSession", "deny"}:
        return False
    if option_id not in {o["option_id"] for o in slot["options"]}:
        return False
    if option_id == "allowSession" and slot["scope"] is None:
        return False
    _pending.pop(request_id, None)
    slot["option_id"] = option_id
    slot["approved"] = option_id != "deny"
    slot["feedback"] = feedback if option_id == "deny" else None
    slot["event"].set()
    return True


async def authorize_tool(deps, tool_name: str, tool_input: dict, *, data_dir=None) -> str | None:
    """现有策略先判定；会话允许只能覆盖ask，不能覆盖硬拒或明确deny。"""
    if deps.stop_flag():
        return "Error: 操作已停止"
    result = evaluate(data_dir, tool_name, tool_input)
    decision, reason = result["decision"], result["reason"]
    if decision == "deny":
        return f"Error: 已被安全策略拒绝：{reason}"
    if decision == "allow" or session_permissions.allows(deps.recorder.session_id, tool_name, tool_input):
        return None
    outcome = await deps.approver(tool_name, tool_input, reason or "该操作需要确认")
    if deps.stop_flag():
        return "Error: 操作已停止"
    if not outcome["approved"]:
        return f"Error: 用户拒绝了该操作：{outcome['denial_reason'] or reason}"
    # 等待期间项目规则可能改变，执行前再确认硬拒/明确拒绝仍优先。
    latest = evaluate(data_dir, tool_name, tool_input)
    if latest["decision"] == "deny":
        return f"Error: 已被安全策略拒绝：{latest['reason']}"
    return None


class InteractiveApprover:
    """挂起时直推SSE；收到有效决定且通过停止检查后才保存会话范围。"""

    def __init__(self, db, session_id: str, recorder, queue: asyncio.Queue,
                 stop_event: asyncio.Event | None = None) -> None:
        self.db = db
        self.session_id = session_id
        self.recorder = recorder
        self.queue = queue
        self.stop_event = stop_event

    async def __call__(self, tool_name: str, tool_input: dict, reason: str) -> dict:
        if self.stop_event is not None and self.stop_event.is_set():
            return {"approved": False, "denial_reason": "操作已停止"}
        request_id = new_id()
        # 先注册再发布，快速回应也能命中槽位；展示和保存使用同一个范围快照。
        slot = register(request_id, tool_name, tool_input, session_id=self.session_id,
                        stop_event=self.stop_event)
        options = slot["options"]
        _events.setdefault(self.session_id, {})[request_id] = {
            'turn_id': self.recorder.turn_id, 'request_id': request_id, 'tool': tool_name,
            'input': deepcopy(tool_input), 'reason': reason, 'status': 'requested', 'time': now_ms(),
            'options': options}
        self.queue.put_nowait({"type": "approval_request", "request_id": request_id,
                               "tool": tool_name, "input": tool_input, "reason": reason, "options": options})
        waits = [asyncio.create_task(slot["event"].wait())]
        if self.stop_event is not None:
            waits.append(asyncio.create_task(self.stop_event.wait()))
        received = False
        approved = False
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            received = True
        finally:
            _pending.pop(request_id, None)
            for task in waits:
                task.cancel()
            try:
                await asyncio.gather(*waits, return_exceptions=True)
            except BaseException:
                received = False
                raise
            finally:
                # gather也会让出执行权，必须在它之后重查停止，避免同tick批准/停止留下授权。
                record = _events.get(self.session_id, {}).get(request_id)
                stopped = self.stop_event is not None and self.stop_event.is_set()
                approved = received and slot["approved"] and not stopped and record is not None
                if approved:
                    latest = evaluate(None, tool_name, tool_input)
                    same_scope = slot["scope"] == session_permissions.scope_for(tool_name, tool_input)
                    approved = latest["decision"] != "deny" and same_scope and slot["input"] == tool_input
                if approved and slot["option_id"] == "allowSession":
                    session_permissions.grant(self.session_id, slot["scope"])
                if record is not None:
                    record["status"] = "approved" if approved else "denied"
                self.queue.put_nowait({"type": "approval_resolved", "request_id": request_id,
                                       "approved": approved})
        feedback = slot["feedback"]
        logger.info("审批完成 request_id=%s tool=%s approved=%s", request_id, tool_name, approved)
        denial_reason = f"{reason}；用户反馈：{feedback}" if not approved and feedback else reason
        return {"approved": approved, "denial_reason": denial_reason}
