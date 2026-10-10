"""ZCode SQLite 六表子集；省略未使用能力，不改变保留字段的存储含义。"""
import time
import uuid
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_ms() -> int:
    """ZCode 使用 epoch 毫秒。"""
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = 'session'
    __table_args__ = (Index('session_project_idx', 'project_id'),)
    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=True)
    project_id: Mapped[str] = mapped_column(Text)
    directory: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    time_created: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_updated: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_archived: Mapped[int | None] = mapped_column(Integer)


class Message(Base):
    __tablename__ = 'message'
    __table_args__ = (
        Index('message_session_time_created_id_idx', 'session_id', 'time_created', 'id'),
        Index('message_session_sequence_idx', 'session_id', 'sequence', 'time_created', 'id'),)
    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=True)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey('session.id', ondelete='CASCADE'))
    sequence: Mapped[int | None] = mapped_column(Integer)
    time_created: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_updated: Mapped[int] = mapped_column(Integer, default=now_ms)
    data: Mapped[str] = mapped_column(Text)


class Part(Base):
    __tablename__ = 'part'
    __table_args__ = (
        Index('part_message_id_id_idx', 'message_id', 'id'),
        Index('part_session_idx', 'session_id'),
        Index('part_message_sequence_idx', 'message_id', 'sequence', 'time_created', 'id'),
        Index('part_session_message_sequence_idx', 'session_id', 'message_id', 'sequence'),)
    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=True)
    message_id: Mapped[str] = mapped_column(Text, ForeignKey('message.id', ondelete='CASCADE'))
    session_id: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int | None] = mapped_column(Integer)
    time_created: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_updated: Mapped[int] = mapped_column(Integer, default=now_ms)
    data: Mapped[str] = mapped_column(Text)


class LocalSetting(Base):
    __tablename__ = 'local_setting'
    __table_args__ = (Index('local_setting_scope_idx', 'scope', 'scope_id'),
                      Index('local_setting_namespace_key_idx', 'namespace', 'key'))
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_id: Mapped[str] = mapped_column(Text, primary_key=True)
    namespace: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    time_created: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_updated: Mapped[int] = mapped_column(Integer, default=now_ms)


class Todo(Base):
    __tablename__ = 'todo'
    __table_args__ = (Index('todo_session_idx', 'session_id'),)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey('session.id', ondelete='CASCADE'), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    time_created: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_updated: Mapped[int] = mapped_column(Integer, default=now_ms)


class TurnUsage(Base):
    __tablename__ = 'turn_usage'
    __table_args__ = (CheckConstraint("status in ('running', 'completed', 'error', 'cancelled')"),
                      Index('turn_usage_started_idx', 'started_at'))
    session_id: Mapped[str] = mapped_column(Text, ForeignKey('session.id', ondelete='CASCADE'), primary_key=True)
    turn_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_message_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[int] = mapped_column(Integer)
    completed_at: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text('0'))
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text('0'))
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text('0'))
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text('0'))
