"""ZCode 式最小四表：关系/顺序在列中，消息与部件内容在 JSON 中。

正文仅存 text part；轮次是 metadata.turnId 标签，不是额外容器或列。
不迁移旧库；结构变更后从空库启动。
"""

import time
import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_ms() -> int:
    """统一使用 epoch 毫秒，供 API 与 SQLite 共用。"""
    return int(time.time() * 1000)


def new_id() -> str:
    """生成实体 ID，更新实体时复用原 ID。"""
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    """ORM 公共基类。"""


class Session(Base):
    """会话身份；time_archived 承载已有软删行为，不增加归档功能。

    模型选择和用量由会话记录投影，避免两份累计值在重跑后漂移。
    """
    __tablename__ = "session"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(120), default="")
    time_created: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_updated: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_archived: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)


class Message(Base):
    """一次用户输入或模型响应的元信息；正文不进入 data。

    data: role、modelId、parentID（助手）、metadata.turnId。
    """
    __tablename__ = "message"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("session.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    time_created: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_updated: Mapped[int] = mapped_column(Integer, default=now_ms)
    data: Mapped[str] = mapped_column(String, default="{}")


class Part(Base):
    """消息内容；data.type 区分 text/tool/subtask/todo/error。

    工具使用 callID、tool、state；todo/error 为本项目必需扩展。
    轮次由 message 取得，不在每个 part 重复保存。
    """
    __tablename__ = "part"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(32), ForeignKey("message.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("session.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    time_created: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_updated: Mapped[int] = mapped_column(Integer, default=now_ms)
    data: Mapped[str] = mapped_column(String, default="{}")


class SessionEntry(Base):
    """会话级状态或审计记录；支持按 ID 更新，审计调用仍可追加。

    type 保留当前 turn/approval/compaction/context；data.metadata.turnId
    标识所属轮次，用于回放和重跑清理。
    """
    __tablename__ = "session_entry"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("session.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(80), index=True)
    time_created: Mapped[int] = mapped_column(Integer, default=now_ms)
    time_updated: Mapped[int] = mapped_column(Integer, default=now_ms)
    data: Mapped[str] = mapped_column(String, default="{}")
