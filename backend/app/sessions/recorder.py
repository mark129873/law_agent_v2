"""turn 里程碑落盘器：把主循环的关键节点写入四表（store.py 的调用者）。

为什么单独一层：主循环只管"执行并吐事件"，持久化细节（行结构、id、
工时记账）收在这里，两边都保持简单。工时记账（active_ms）排除审批
等待时间：循环在调审批回调前调 pause_active()，回来后 resume_active()。
"""

from app.models import new_id, now_ms
from app.sessions import store


class TurnRecorder:
    """一个 turn 对应一个 recorder 实例。"""

    def __init__(self, db, session_id: str, model: str, max_tokens: int) -> None:
        self.db = db
        self.session_id = session_id
        self.model = model
        self.max_tokens = max_tokens
        self.turn_id = new_id()
        self._started_at: float | None = None
        self._pause_started: float | None = None
        self._paused_ms: float = 0.0
        self.tokens_used = 0
        # 本轮用户消息的 sequence（compact 边界：在此之前的历史才可被摘要）
        self.first_user_sequence = 0

    @property
    def started_at(self) -> float | None:
        """turn 开始时间（事件用，避免外部摸私有字段）。"""
        return self._started_at

    def add_tokens(self, output_tokens: int) -> None:
        """累计本次 turn 的输出 token（usage 事件驱动）。"""
        self.tokens_used += output_tokens

    # ---------- 生命周期 ----------

    def begin_turn(self, user_text: str) -> str:
        """turn 开始：确保会话行存在、落用户消息、记上下文快照。"""
        store.ensure_session(self.db, self.session_id, self.model, user_text)
        user_row = store.upsert_message(
            self.db, self.session_id, new_id(), "user", {"text": user_text}, self.turn_id
        )
        self.first_user_sequence = user_row.sequence
        store.put_entry(
            self.db,
            self.session_id,
            "context",
            {"model": self.model, "max_tokens": self.max_tokens, "time": now_ms()},
        )
        self._started_at = now_ms()
        return self.turn_id

    def pause_active(self) -> None:
        """暂停工时累计（等待用户审批期间不计入有效工时）。"""
        if self._pause_started is None:
            self._pause_started = now_ms()

    def resume_active(self) -> None:
        """恢复工时累计。"""
        if self._pause_started is not None:
            self._paused_ms += now_ms() - self._pause_started
            self._pause_started = None

    def end_turn(self, state: str) -> dict:
        """turn 收口：写 turn 事实（工作块数据源），累计 token 到会话。

        tokens_used 取本 turn 内 add_tokens 累计的值（usage 事件驱动）。
        """
        self.resume_active()  # 若停在审批等待中收口，先把暂停段结掉
        ended_at = now_ms()
        active_ms = max(0.0, ended_at - (self._started_at or ended_at) - self._paused_ms)
        fact = {
            "turn_id": self.turn_id,
            "started_at": self._started_at or ended_at,
            "ended_at": ended_at,
            "active_ms": active_ms,
            "state": state,
            "tokens_used": self.tokens_used,
        }
        store.put_entry(self.db, self.session_id, "turn", fact)
        # 会话累计 token：直接累加（重新生成时会由 BE-9 重算修正）
        from app.models import Session

        row = self.db.get(Session, self.session_id)
        if row is not None:
            row.tokens_used += self.tokens_used
            row.updated_at = now_ms()
            self.db.commit()
        return fact

    # ---------- 消息与部件 ----------

    def step_message(self) -> str:
        """assistant 模型步开始：建 message 行（里程碑语义，ZCode 同款）。"""
        row = store.upsert_message(
            self.db, self.session_id, new_id(), "assistant", {"text": ""}, self.turn_id
        )
        return row.id

    def write_text_part(self, message_id: str, text: str) -> str:
        """该步响应结束时整段落 text part（流式增量不落库）。"""
        part_id = new_id()
        store.upsert_part(
            self.db, self.session_id, message_id, part_id, "text", {"text": text}, self.turn_id
        )
        return part_id

    def upsert_tool_part(self, message_id: str, part_id: str, data: dict) -> None:
        """工具部件生命周期：同一 part_id 反复推进（pending→running→completed/...）。"""
        store.upsert_part(
            self.db, self.session_id, message_id, part_id, "tool_call", data, self.turn_id
        )

    def write_subtask_part(self, message_id: str, data: dict) -> str:
        """subtask 卡片落盘（BE-8 使用，先备好通道）。"""
        part_id = new_id()
        store.upsert_part(
            self.db, self.session_id, message_id, part_id, "subtask", data, self.turn_id
        )
        return part_id

    def write_todo_part(self, message_id: str, items: list) -> str:
        """任务板快照落盘（BE-8 使用）。"""
        part_id = new_id()
        store.upsert_part(
            self.db, self.session_id, message_id, part_id, "todo", {"items": items}, self.turn_id
        )
        return part_id

    def write_error_part(self, message_id: str, message: str) -> str:
        """错误卡片落盘（API 失败等）。"""
        part_id = new_id()
        store.upsert_part(
            self.db, self.session_id, message_id, part_id, "error", {"message": message}, self.turn_id
        )
        return part_id
