"""交互审批：SSE 推请求（带动态选项）→ 前端 POST 决定 → 唤醒循环。

两个组件：
- 进程内 pending 注册表：request_id -> (asyncio.Event, 决定槽位, tool/input/options)。
  为什么能支持"刷新页面后仍可响应"：turn 的等待协程持有注册表里的
  Event，新页面 POST /approval 只要拿到同一个 request_id 就能唤醒，
  与连接无关。槽位同时携带 tool/input/options——"总是允许"存规则、
  "完全访问"切模式都在 resolve 侧凭注册表信息完成。
- InteractiveApprover：loop.py 的 approver 回调实现。它绕过循环直接把
  approval_request / approval_resolved 事件写进 SSE 队列——因为循环此刻
  正阻塞在审批等待上，事件必须由这里推，前端才能先弹窗后等待。

决定返回值（ZCode buildPermissionDeniedContent 同语义）：
approved=False 时 denial_reason 携带用户反馈原文，作为 tool_result 喂回模型。
"""

import asyncio
import logging

from app.agent.permission_service import derive_rule
from app.models import new_id, now_ms
from app.sessions import store

logger = logging.getLogger(__name__)

# request_id -> {"event", "approved", "feedback", "tool", "input", "options", "full_access"}
_pending: dict[str, dict] = {}


def register(
    request_id: str,
    tool: str | None = None,
    input_data: dict | None = None,
    options: list[dict] | None = None,
    full_access: bool = False,
) -> dict:
    """登记一个待决审批（携带 tool/input/options 供 resolve 侧派生规则/切模式）。"""
    slot = {
        "event": asyncio.Event(),
        "approved": False,
        "feedback": None,
        "tool": tool,
        "input": input_data,
        "options": options or [],
        "full_access": full_access,
    }
    _pending[request_id] = slot
    return slot


def get_request(request_id: str) -> dict | None:
    """取待决审批的登记信息（不弹出；resolve 后即不可见）。"""
    return _pending.get(request_id)


def resolve(request_id: str, approved: bool, feedback: str | None = None) -> bool:
    """提交决定并唤醒等待方；取出式（重复提交/未知 id 返回 False）。"""
    slot = _pending.pop(request_id, None)
    if slot is None:
        return False
    slot["approved"] = approved
    slot["feedback"] = feedback
    slot["event"].set()
    return True


def build_options(tool_name: str, tool_input: dict, allow_full_access: bool = True) -> list[dict]:
    """构造动态选项（排序照抄 ZCode：allowOnce → fullAccess → allowAlways → deny）。

    allowAlways 仅 bash 触发时投放（规则推导只支持 bash 命令），
    且 content 按高危根命令安全约束生成（精确或前缀）。
    回放恢复路径（load_replay 的 pending_approval）也用它补齐选项。
    """
    if tool_name == "exit_plan_mode":
        # 计划审批：批准 / 要求修改（反馈会喂回模型改计划）
        return [
            {"option_id": "approve", "label": "批准计划，开始实现"},
            {"option_id": "deny", "label": "要求修改"},
        ]
    options: list[dict] = [{"option_id": "allowOnce", "label": "仅本次允许"}]
    if allow_full_access:
        options.append({"option_id": "fullAccess", "label": "完全访问"})
    rule = derive_rule(tool_name, tool_input)
    if rule:
        options.append({"option_id": "allowAlways", "label": "总是允许", "content": rule["content"]})
    options.append({"option_id": "deny", "label": "拒绝"})
    return options


class InteractiveApprover:
    """loop.py 的 approver 回调：挂起等待用户决定，结果落痕并推事件。

    allow_full_access=False（子助手调用）时不投放"完全访问"选项
    （ZCode：origin=subagent 的请求不投放 fullAccess）。
    """

    def __init__(self, db, session_id: str, recorder, queue: asyncio.Queue) -> None:
        self.db = db
        self.session_id = session_id
        self.recorder = recorder  # 取 turn_id 与工时记账由循环负责（pause/resume 在 loop）
        self.queue = queue

    async def __call__(
        self, tool_name: str, tool_input: dict, reason: str, *, allow_full_access: bool = True
    ) -> dict:
        request_id = new_id()
        options = build_options(tool_name, tool_input, allow_full_access)

        # 1. 落"待决"事实（刷新后回放可恢复弹窗的数据源）
        store.put_entry(
            self.db, self.session_id, "approval",
            {
                "turn_id": self.recorder.turn_id,
                "request_id": request_id,
                "tool": tool_name,
                "input": tool_input,
                "reason": reason,
                "status": "requested",
                "time": now_ms(),
            },
            turn_id=self.recorder.turn_id,
        )
        # 2. 推审批请求给前端（循环阻塞中，由这里直推队列）
        await self.queue.put(
            {
                "type": "approval_request",
                "request_id": request_id,
                "tool": tool_name,
                "input": tool_input,
                "reason": reason,
                "options": options,
                "full_access": allow_full_access,
            }
        )

        # 3. 挂起等待（工时暂停由循环包在 pause/resume 里）
        slot = register(request_id, tool_name, tool_input, options, allow_full_access)
        await slot["event"].wait()
        approved = slot["approved"]
        feedback = slot.get("feedback")

        # 4. 决定落痕（第二条事实：requested + resolved 都留痕）
        store.put_entry(
            self.db, self.session_id, "approval",
            {
                "turn_id": self.recorder.turn_id,
                "request_id": request_id,
                "tool": tool_name,
                "input": tool_input,
                "reason": reason,
                "status": "approved" if approved else "denied",
                "time": now_ms(),
            },
            turn_id=self.recorder.turn_id,
        )
        # 5. 通知前端结果（审批卡片翻转状态）
        await self.queue.put(
            {"type": "approval_resolved", "request_id": request_id, "approved": approved}
        )
        logger.info("审批完成 request_id=%s tool=%s approved=%s", request_id, tool_name, approved)

        # 拒绝时把用户反馈拼进喂回模型的理由（ZCode buildPermissionDeniedContent 同语义）
        denial_reason = reason
        if not approved and feedback:
            denial_reason = f"{reason}；用户反馈：{feedback}"
        return {"approved": approved, "denial_reason": denial_reason}
