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
        row.time_updated = now_ms()


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
        time_created=now_ms(),
        time_updated=now_ms(),
    )
    db.add(row)
    db.flush()
    put_entry(db, session_id, "context", {"model": model})
    return row


def get_session(db: DbSession, session_id: str) -> Session | None:
    """取未删除的会话行；已删或不存在返回 None。"""
    row = db.get(Session, session_id)
    if row is None or row.time_archived is not None:
        return None
    return row


def list_sessions(db: DbSession, limit: int = 200) -> list[Session]:
    """会话列表：过滤软删，按更新时间倒序（产品规则）。"""
    stmt = (
        select(Session)
        .where(Session.time_archived.is_(None))
        .order_by(Session.time_updated.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def soft_delete_session(db: DbSession, session_id: str) -> bool:
    """软删除：置 deleted_at。用户视角是永久删除（无归档），库里保留行。

    返回是否真的删了（不存在或已删返回 False）。
    """
    row = db.get(Session, session_id)
    if row is None or row.time_archived is not None:
        return False
    row.time_archived = now_ms()
    db.commit()
    return True


def turn_id(row) -> str:
    """读取 JSON 轮次标签；无旧列回退，因为本次只支持空库。"""
    return _load(row.data).get("metadata", {}).get("turnId", "")


def message_role(row: Message) -> str:
    """消息身份从元信息读取。"""
    return _load(row.data).get("role", "")


def part_kind(row: Part) -> str:
    """将存储类型转换为现有界面/工具协议名称。"""
    kind = _load(row.data).get("type", "")
    return "tool_call" if kind == "tool" else kind


def part_data(row: Part) -> dict:
    """工具存储与 API 形状解耦；调用者拿到的对象修改后仍需显式保存。"""
    data = _load(row.data)
    if data.get("type") == "tool":
        return {**data.get("state", {}), "name": data.get("tool", ""),
                "tool_call_id": data.get("callID", row.id)}
    return data


def _tag(data: dict, label: str) -> dict:
    """保留其他元信息，仅规范化当前轮次标签。"""
    result = dict(data)
    result.pop("turn_id", None)
    result["metadata"] = {**result.get("metadata", {}), "turnId": label}
    return result


def upsert_message(db: DbSession, session_id: str, message_id: str,
                   role: str, data: dict, turn_id: str, *, commit: bool = True) -> Message:
    """只写元信息；同 ID 更新不允许改变会话归属或顺序。"""
    if "text" in data:
        raise ValueError("正文必须通过 text part 保存")
    payload = _tag({**data, "role": role}, turn_id)
    row = db.get(Message, message_id)
    if row is None:
        row = Message(id=message_id, session_id=session_id,
                      sequence=_next_sequence(db, Message, session_id), data=_dump(payload))
        db.add(row)
    else:
        if row.session_id != session_id:
            raise ValueError("消息 ID 已属于其他会话")
        row.data = _dump(payload)
    row.time_updated = now_ms()
    touch_session(db, session_id)
    db.flush()
    if commit:
        db.commit()
    return row


def upsert_part(db: DbSession, session_id: str, message_id: str, part_id: str,
                kind: str, data: dict, turn_id: str = "", *, commit: bool = True) -> Part:
    """消息内稳定排序；写入前验证父消息与会话，避免跨会话内容混入。"""
    parent = db.get(Message, message_id)
    if parent is None or parent.session_id != session_id:
        raise ValueError("部件所属消息不存在或不属于该会话")
    if turn_id and _load(parent.data).get("metadata", {}).get("turnId", "") != turn_id:
        raise ValueError("部件轮次与所属消息不一致")
    if kind == "tool_call":
        state = {k: v for k, v in data.items() if k not in ("name", "tool_call_id", "type")}
        payload = {"type": "tool", "callID": data.get("tool_call_id", part_id),
                   "tool": data.get("name", ""), "state": state}
    else:
        payload = {**data, "type": kind}
    row = db.get(Part, part_id)
    if row is None:
        current = db.execute(select(func.max(Part.sequence)).where(Part.message_id == message_id)).scalar()
        row = Part(id=part_id, message_id=message_id, session_id=session_id,
                   sequence=(current or 0) + 1, data=_dump(payload))
        db.add(row)
    else:
        if row.message_id != message_id or row.session_id != session_id:
            raise ValueError("部件 ID 已属于其他消息")
        row.data = _dump(payload)
    row.time_updated = now_ms()
    touch_session(db, session_id)
    db.flush()
    if commit:
        db.commit()
    return row


def save_user_message(db: DbSession, session_id: str, message_id: str,
                      text: str, turn_id: str, model: str = "") -> Message:
    """用户元信息与正文原子提交，避免中断留下没有正文的用户消息。"""
    try:
        row = upsert_message(db, session_id, message_id, "user", {"modelId": model}, turn_id, commit=False)
        # 固定部件 ID 让同一输入的重试幂等；普通助手正文使用独立随机 ID。
        upsert_part(db, session_id, message_id, message_id, "text", {"text": text}, turn_id, commit=False)
        db.commit()
        return row
    except Exception:
        db.rollback()
        raise


def put_entry(db: DbSession, session_id: str, entry_type: str, data: dict,
              turn_id: str = "", *, entry_id: str | None = None,
              touch: bool = True) -> SessionEntry:
    """省略 ID 时追加审计记录；指定同 ID 时更新，保留创建时间。"""
    label = turn_id or data.get("turn_id", "") or data.get("metadata", {}).get("turnId", "")
    payload = _tag(data, label) if label else dict(data)
    row = db.get(SessionEntry, entry_id) if entry_id else None
    if row is None:
        row = SessionEntry(id=entry_id or new_id(), session_id=session_id,
                           type=entry_type, data=_dump(payload))
        db.add(row)
    else:
        if row.session_id != session_id:
            raise ValueError("会话记录 ID 已属于其他会话")
        row.type, row.data = entry_type, _dump(payload)
    row.time_updated = now_ms()
    if touch:
        touch_session(db, session_id)
    db.commit()
    return row


def find_last_user_message(db: DbSession, session_id: str) -> Message | None:
    """用户角色从 JSON 查询，仍按稳定 sequence 选择重跑锚点。"""
    return db.execute(select(Message).where(
        Message.session_id == session_id, func.json_extract(Message.data, "$.role") == "user"
    ).order_by(Message.sequence.desc()).limit(1)).scalar()


def rollback_turn(db: DbSession, session_id: str, turn_id: str, from_sequence: int) -> int:
    """保留用户及其正文；级联清理助手部件，再按 JSON 标签删除本轮事实。"""
    result = db.execute(delete(Message).where(
        Message.session_id == session_id,
        func.json_extract(Message.data, "$.role") == "assistant",
        Message.sequence >= from_sequence,
    ))
    db.execute(delete(SessionEntry).where(
        SessionEntry.session_id == session_id,
        func.json_extract(SessionEntry.data, "$.metadata.turnId") == turn_id,
    ))
    db.commit()
    return result.rowcount or 0


def retag_message(db: DbSession, row: Message, label: str) -> None:
    """重跑只改消息轮次标签；其 text part 通过 message_id 自动随之归属。"""
    row.data = _dump(_tag(_load(row.data), label))
    row.time_updated = now_ms()
    db.commit()


def list_entries(db: DbSession, session_id: str, entry_type: str | None = None) -> list[SessionEntry]:
    """时间相同时按 SQLite rowid 稳定排序，审批最新状态不会随机翻转。"""
    from sqlalchemy import literal_column
    stmt = select(SessionEntry).where(SessionEntry.session_id == session_id)
    if entry_type is not None:
        stmt = stmt.where(SessionEntry.type == entry_type)
    return list(db.execute(stmt.order_by(SessionEntry.time_created, literal_column("session_entry.rowid"))).scalars())


def entry_data(entry: SessionEntry) -> dict:
    """投影为现有回放协议；数据库仅保存 metadata.turnId。"""
    data = _load(entry.data)
    if turn_id(entry):
        data["turn_id"] = turn_id(entry)
    return data


def session_info(db: DbSession, row: Session) -> dict:
    """会话展示字段从事实投影；回滚后自然排除已删除轮次的用量。"""
    info = {"id": row.id, "title": row.title, "model": "",
            "created_at": row.time_created, "updated_at": row.time_updated,
            "tokens_used": 0, "input_tokens": 0}
    for entry in list_entries(db, row.id):
        data = entry_data(entry)
        if entry.type == "context":
            info["model"] = data.get("model", info["model"])
        elif entry.type == "turn":
            info["tokens_used"] += int(data.get("tokens_used", 0))
            info["input_tokens"] += int(data.get("input_tokens", 0))
    return info
