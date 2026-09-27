"""交互审批：SSE 推请求 → 前端 POST 决定 → 唤醒循环。

两个组件：
- 进程内 pending 注册表：request_id -> (asyncio.Event, 决定槽位)。
  为什么能支持"刷新页面后仍可响应"：turn 的等待协程持有注册表里的
  Event，新页面 POST /approval 只要拿到同一个 request_id 就能唤醒，
  与连接无关。
- InteractiveApprover：loop.py 的 approver 回调实现。它绕过循环直接把
  approval_request / approval_resolved 事件写进 SSE 队列——因为循环此刻
  正阻塞在审批等待上，事件必须由这里推，前端才能先弹窗后等待。
"""

import asyncio
import logging

from app.models import new_id, now_ms
from app.sessions import store

logger = logging.getLogger(__name__)

# request_id -> {"event": asyncio.Event, "approved": bool}
_pending: dict[str, dict] = {}


def register(request_id: str) -> dict:
    """登记一个待决审批。"""
    slot = {"event": asyncio.Event(), "approved": False}
    _pending[request_id] = slot
    return slot


def resolve(request_id: str, approved: bool) -> bool:
    """提交决定并唤醒等待方；取出式（重复提交/未知 id 返回 False）。"""
    slot = _pending.pop(request_id, None)
    if slot is None:
        return False
    slot["approved"] = approved
    slot["event"].set()
    return True


class InteractiveApprover:
    """loop.py 的 approver 回调：挂起等待用户决定，结果落痕并推事件。"""

    def __init__(self, db, session_id: str, recorder, queue: asyncio.Queue) -> None:
        self.db = db
        self.session_id = session_id
        self.recorder = recorder  # 取 turn_id 与工时记账由循环负责（pause/resume 在 loop）
        self.queue = queue

    async def __call__(self, tool_name: str, tool_input: dict, reason: str) -> bool:
        request_id = new_id()

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
            }
        )

        # 3. 挂起等待（工时暂停由循环包在 pause/resume 里）
        slot = register(request_id)
        await slot["event"].wait()
        approved = slot["approved"]

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
        return approved
