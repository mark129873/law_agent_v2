"""turn 记录器：六表子集，消息元信息与轮次累计分开保存。"""
from app.models import Message, TurnUsage, new_id, now_ms
from app.sessions import store


class TurnRecorder:
    def __init__(self, db, session_id: str, model: str, max_tokens: int) -> None:
        self.db, self.session_id, self.model = db, session_id, model
        self.max_tokens = max_tokens
        self.turn_id = new_id()
        self._started_at = None
        self._message_id = None
        self.tokens_used = self.tokens_input = 0
        self.cache_read_tokens = self.cache_creation_tokens = 0
        self._last_input = 0
        self.first_user_sequence = 0

    @property
    def started_at(self):
        return self._started_at

    def add_usage(self, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0,
                  cache_creation_tokens: int = 0, *, subtask: bool = False) -> None:
        """子模型只入整轮累计，不能覆盖主模型消息的 usage 或上下文占用。"""
        self.tokens_used += output_tokens
        self.tokens_input += input_tokens
        self.cache_read_tokens += cache_read_tokens
        self.cache_creation_tokens += cache_creation_tokens
        if not subtask:
            self._last_input = input_tokens
            if self._message_id:
                row = self.db.get(Message, self._message_id)
                data = store._load(row.data)
                data['tokens'] = {'input': input_tokens, 'output': output_tokens,
                                  'cache': {'read': cache_read_tokens, 'write': cache_creation_tokens}}
                row.data = store._dump(data)
                self.db.commit()

    def begin_turn(self, user_text: str, persist_user: bool = True, user_sequence=None) -> str:
        if persist_user:
            store.ensure_session(self.db, self.session_id, self.model, user_text)
            user = store.save_user_message(self.db, self.session_id, new_id(), user_text, self.turn_id, self.model)
            self.first_user_sequence = user.sequence
        else:
            user = store.find_last_user_message(self.db, self.session_id)
            self.first_user_sequence = user_sequence if user_sequence is not None else user.sequence
        self._started_at = now_ms()
        self.db.add(TurnUsage(session_id=self.session_id, turn_id=self.turn_id, user_message_id=user.id,
                              status='running', started_at=self._started_at))
        self.db.commit()
        return self.turn_id

    def end_turn(self, state: str) -> dict:
        """总耗时包含审批等待；状态使用 ZCode 词表，API 在边界转换。"""
        ended = now_ms()
        row = self.db.get(TurnUsage, (self.session_id, self.turn_id))
        if row is None:
            row = TurnUsage(session_id=self.session_id, turn_id=self.turn_id, started_at=self._started_at or ended)
            self.db.add(row)
        row.status = {'success': 'completed', 'failed': 'error', 'stopped': 'cancelled'}[state]
        row.completed_at = ended
        row.duration_ms = max(0, ended - row.started_at)
        row.input_tokens, row.output_tokens = self.tokens_input, self.tokens_used
        row.cache_read_input_tokens, row.cache_creation_input_tokens = self.cache_read_tokens, self.cache_creation_tokens
        self.db.commit()
        return store.turn_fact(row)

    def step_message(self) -> str:
        parent = store.find_last_user_message(self.db, self.session_id)
        row = store.upsert_message(self.db, self.session_id, new_id(), 'assistant',
                                  {'modelId': self.model, 'parentID': parent.id if parent else None}, self.turn_id)
        self._message_id = row.id
        return row.id

    def finish_message(self, message_id: str, finish: str = 'stop') -> None:
        row = self.db.get(Message, message_id)
        data = store._load(row.data)
        data['time']['completed'] = now_ms()
        data['finish'] = finish
        row.time_updated = data['time']['completed']
        row.data = store._dump(data)
        self.db.commit()

    def write_text_part(self, message_id: str, text: str) -> str:
        pid = new_id()
        store.upsert_part(self.db, self.session_id, message_id, pid, 'text', {'text': text}, self.turn_id)
        return pid

    def upsert_tool_part(self, message_id: str, part_id: str, data: dict) -> None:
        store.upsert_part(self.db, self.session_id, message_id, part_id, 'tool_call', data, self.turn_id)

    def write_todos(self, items: list) -> None:
        store.replace_todos(self.db, self.session_id, items)

    def write_error_part(self, message_id: str, message: str) -> str:
        """保留调用入口名；实际错误属于 message.error，不创建 error part。"""
        row = self.db.get(Message, message_id)
        data = store._load(row.data)
        data['error'] = {'name': 'APIError', 'data': {'message': message}}
        row.data = store._dump(data)
        self.finish_message(message_id, 'error')
        return message_id
