"""会话写入层：ZCode session-store 同款的里程碑式实时 upsert。

核心规则（为什么这么写）：
1. 全部写入用"按 id upsert"（不存在则插入，存在则更新），同一实体的生命周期
   变化（工具 pending→running→completed）反复写同一行，天然幂等；
2. sequence 只在首次插入时分配（max+1），更新时绝不触碰——ZCode 用这条规则
   防止二次保存导致时间线漂移；
3. 每次写后 touchSession 更新 updated_at，会话列表的排序依据；
4. draft 语义：ensure_session 在首条用户消息时才建 session 行（产品决策）。
"""

import json

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from app.models import Message, Part, Session, SessionEntry, new_id, now_ms


def _dump(data: dict) -> str:
    """dict → JSON 字符串（统一 ensure_ascii=False，库里存中文原文便于排查）。"""
    return json.dumps(data, ensure_ascii=False)


def _load(raw: str) -> dict:
    """JSON 字符串 → dict；坏数据不炸接口，返回空 dict 并由上层日志兜底。"""
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def touch_session(db: DbSession, session_id: str) -> None:
    """更新会话 updated_at（列表排序依据）。会话行不存在时静默跳过（draft 期）。"""
    row = db.get(Session, session_id)
    if row is not None:
        row.updated_at = now_ms()


def _next_sequence(db: DbSession, model, session_id: str) -> int:
    """取该会话在指定表内的下一个 sequence（首次插入专用）。"""
    current = db.execute(
        select(func.max(model.sequence)).where(model.session_id == session_id)
    ).scalar()
    return (current or 0) + 1


def ensure_session(
    db: DbSession, session_id: str, model: str, first_user_text: str
) -> Session:
    """draft 语义：会话行不存在则创建（标题=首条用户消息截断 30 字）。

    为什么不在 POST /sessions 时建行：与 ZCode 一致，空会话不值得落库；
    首条消息发出时才成为真实会话。
    """
    row = db.get(Session, session_id)
    if row is not None:
        return row
    row = Session(
        id=session_id,
        title=(first_user_text or "新会话").replace("\n", " ")[:30],
        model=model,
        created_at=now_ms(),
        updated_at=now_ms(),
    )
    db.add(row)
    db.commit()
    return row


def get_session(db: DbSession, session_id: str) -> Session | None:
    """取未删除的会话行；已删或不存在返回 None。"""
    row = db.get(Session, session_id)
    if row is None or row.deleted_at is not None:
        return None
    return row


def list_sessions(db: DbSession, limit: int = 200) -> list[Session]:
    """会话列表：过滤软删，按更新时间倒序（产品规则）。"""
    stmt = (
        select(Session)
        .where(Session.deleted_at.is_(None))
        .order_by(Session.updated_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def soft_delete_session(db: DbSession, session_id: str) -> bool:
    """软删除：置 deleted_at。用户视角是永久删除（无归档），库里保留行。

    返回是否真的删了（不存在或已删返回 False）。
    """
    row = db.get(Session, session_id)
    if row is None or row.deleted_at is not None:
        return False
    row.deleted_at = now_ms()
    db.commit()
    return True


def upsert_message(
    db: DbSession,
    session_id: str,
    message_id: str,
    role: str,
    data: dict,
    turn_id: str,
) -> Message:
    """按 id upsert 消息行：首次插入分配 sequence，更新时保留原 sequence。"""
    row = db.get(Message, message_id)
    if row is None:
        row = Message(
            id=message_id,
            session_id=session_id,
            sequence=_next_sequence(db, Message, session_id),
            turn_id=turn_id,
            role=role,
            data=_dump(data),
        )
        db.add(row)
    else:
        # 里程碑语义：正文可在步内增长重写，但 sequence 与 turn 归属不变
        row.data = _dump(data)
    row.time_updated = now_ms()
    touch_session(db, session_id)
    db.commit()
    return row


def upsert_part(
    db: DbSession,
    session_id: str,
    message_id: str,
    part_id: str,
    kind: str,
    data: dict,
    turn_id: str,
) -> Part:
    """按 id upsert 部件行：工具生命周期逐态写同一 partID。"""
    row = db.get(Part, part_id)
    if row is None:
        # part 的 sequence 在所属 message 内递增
        current = db.execute(
            select(func.max(Part.sequence)).where(Part.message_id == message_id)
        ).scalar()
        row = Part(
            id=part_id,
            message_id=message_id,
            session_id=session_id,
            sequence=(current or 0) + 1,
            turn_id=turn_id,
            kind=kind,
            data=_dump(data),
        )
        db.add(row)
    else:
        # 状态推进：data 整体覆盖（写入方负责带全字段），sequence 保留
        row.data = _dump(data)
    row.time_updated = now_ms()
    touch_session(db, session_id)
    db.commit()
    return row


def put_entry(
    db: DbSession, session_id: str, entry_type: str, data: dict, turn_id: str = ""
) -> SessionEntry:
    """追加一条会话级事实（turn/approval/compaction/context）。事实不可变，只插入。"""
    row = SessionEntry(
        id=new_id(),
        session_id=session_id,
        type=entry_type,
        turn_id=turn_id,
        data=_dump(data),
    )
    db.add(row)
    touch_session(db, session_id)
    db.commit()
    return row


def find_last_user_message(db: DbSession, session_id: str) -> Message | None:
    """取最后一条用户消息（重新生成的锚点）。"""
    row = db.execute(
        select(Message)
        .where(Message.session_id == session_id, Message.role == "user")
        .order_by(Message.sequence.desc())
        .limit(1)
    ).scalar()
    return row


def rollback_turn(db: DbSession, session_id: str, turn_id: str, from_sequence: int) -> int:
    """回滚一轮：删除该轮的 assistant 消息（级联删 parts）与该轮全部事实行。

    from_sequence = 该轮用户消息的 sequence——只删 >= 它的 assistant 行；
    用户消息保留（重新生成语义：保留请求、重跑回复）。
    返回删除的 assistant 消息条数。
    """
    result = db.execute(
        delete(Message).where(
            Message.session_id == session_id,
            Message.role == "assistant",
            Message.sequence >= from_sequence,
        )
    )
    # 该轮事实（turn/approval/compaction/context）
    db.execute(
        delete(SessionEntry).where(
            SessionEntry.session_id == session_id, SessionEntry.turn_id == turn_id
        )
    )
    db.commit()
    return result.rowcount or 0


def list_entries(db: DbSession, session_id: str, entry_type: str | None = None) -> list[SessionEntry]:
    """按类型读事实（time_created 升序）。"""
    stmt = select(SessionEntry).where(SessionEntry.session_id == session_id)
    if entry_type is not None:
        stmt = stmt.where(SessionEntry.type == entry_type)
    stmt = stmt.order_by(SessionEntry.time_created.asc())
    return list(db.execute(stmt).scalars())


def entry_data(entry: SessionEntry) -> dict:
    """事实行的 JSON 解包。"""
    return _load(entry.data)
