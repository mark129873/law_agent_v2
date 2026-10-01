"""实体表定义：ZCode session-store 同款的四表结构。

设计要点（docs/ARCHITECTURE.md §3）：
- SQLite 单库是会话内容的唯一事实源，所有可变结构数据放 data JSON 列；
- sequence 决定时间线顺序：首次取 max+1，之后永不改动（冲突时原样保留），
  这是 ZCode 防止"二次保存导致时间线漂移"的关键规则；
- 时间戳统一用 epoch 毫秒浮点数（time.time() * 1000），JSON 序列化直接可用；
- 软删除：session.deleted_at 非空即视为已删（用户视角是永久删除，无归档）。
"""

import time
import uuid

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_ms() -> float:
    """当前 epoch 毫秒（全部时间戳的统一来源）。"""
    return time.time() * 1000.0


def new_id() -> str:
    """生成 32 位随机 id（uuid4 hex，无连字符）。"""
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    """全部 ORM 模型的公共基类。"""


class Session(Base):
    """会话表：一行一个会话。

    title 由首条用户消息截断 30 字生成，不可重命名（产品决策）。
    tokens_used 是累计缓存值，turn 收口时由回放重算后更新。
    """

    __tablename__ = "session"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now_ms)
    updated_at: Mapped[float] = mapped_column(Float, default=now_ms)
    # 软删除标记：非空 = 已删。列表/回放一律过滤。
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)


class Message(Base):
    """消息表：一行一条用户/助手消息。

    data JSON 结构：{"text": "..."}（v1 消息正文就是文本）。
    assistant 消息在"每个模型步开始"时建行（ZCode 里程碑语义），
    正文随后通过更新 data 填充。
    """

    __tablename__ = "message"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("session.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    # turn 标签（ZCode 设计：turn 是行上的标签不是容器），回放按它分组
    turn_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    data: Mapped[str] = mapped_column(String, default="{}")
    time_created: Mapped[float] = mapped_column(Float, default=now_ms)
    time_updated: Mapped[float] = mapped_column(Float, default=now_ms)


class Part(Base):
    """消息部件表：挂在 message 下的可变子项。

    kind 与 data JSON 约定：
    - text:      {"text": "..."}                        assistant 正文（整段写入）
    - tool_call: {"name","input","status","output",...} 工具调用，按生命周期逐态 upsert
    - subtask:   {"goal","status","output",...}         子助手调用
    - todo:      {"items":[...]}                        任务板快照
    """

    __tablename__ = "part"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("message.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("session.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    # turn 标签（同 message），回放按它归组
    turn_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    kind: Mapped[str] = mapped_column(String(20))
    data: Mapped[str] = mapped_column(String, default="{}")
    time_created: Mapped[float] = mapped_column(Float, default=now_ms)
    time_updated: Mapped[float] = mapped_column(Float, default=now_ms)


class SessionEntry(Base):
    """会话事实表：不挂在具体消息上的会话级事实。

    type 与 data JSON 约定（docs/ARCHITECTURE.md §3.4）：
    - turn:       {"turn_id","started_at","ended_at","active_ms","state"}  工作块数据源
    - approval:   {"request_id","tool","approved","time"}                  审批留痕
    - compaction: {"summary_message_id","tokens_before","tokens_after"}     压缩事实
    - context:    {"model","max_tokens","system_prompt_mtime"}              每 turn 上下文快照

    turn_id 列：事实所属轮次的标签（重新生成回滚时按它删除该轮事实）。
    """

    __tablename__ = "session_entry"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("session.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(20), index=True)
    turn_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    data: Mapped[str] = mapped_column(String, default="{}")
    time_created: Mapped[float] = mapped_column(Float, default=now_ms)
