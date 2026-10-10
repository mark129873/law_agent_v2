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

from app.models import Message, Part, Session, Todo, TurnUsage, new_id, now_ms


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
    return (current if current is not None else -1) + 1


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
    from app.config import settings
    from app.sessions.execution_state import project_id, project_directory
    row = Session(
        project_id=project_id(settings.data_dir), directory=project_directory(settings.data_dir),
        id=session_id,
        title=(first_user_text or "新会话").replace("\n", " ")[:30],
        time_created=now_ms(),
        time_updated=now_ms(),
    )
    db.add(row)
    db.flush()
    db.commit()
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
    """软删除：置 time_archived。用户视角是永久删除（无归档），库里保留行。

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
    return _load(row.data).get("anchor", {}).get("turnId", "")


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
        state = data.get("state", {})
        return {**state, "output": state.get("output", state.get("error", "")), "name": data.get("tool", ""),
                "tool_call_id": data.get("callID", row.id)}
    return data


def _tag(data: dict, label: str) -> dict:
    """保留其他元信息，仅规范化当前轮次标签。"""
    result = dict(data)
    result.pop("turn_id", None)
    result["anchor"] = {**result.get("anchor", {}), "turnId": label}
    return result


def upsert_message(db: DbSession, session_id: str, message_id: str,
                   role: str, data: dict, turn_id: str, *, commit: bool = True) -> Message:
    """只写元信息；同会话更新保留顺序，改归属时重新分配序号。"""
    if "text" in data:
        raise ValueError("正文必须通过 text part 保存")
    payload = _tag({**data, "role": role}, turn_id)
    payload.setdefault("time", {"created": now_ms()})
    row = db.get(Message, message_id)
    if row is None:
        row = Message(id=message_id, session_id=session_id,
                      sequence=_next_sequence(db, Message, session_id), data=_dump(payload),
                      time_created=payload["time"]["created"])
        db.add(row)
    else:
        if row.session_id != session_id:
            row.sequence = _next_sequence(db, Message, session_id)
            row.session_id = session_id
        payload["time"]["created"] = row.time_created
        row.data = _dump(payload)
    row.time_updated = payload["time"].get("completed", now_ms()) if role == "assistant" else row.time_created
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
    if turn_id and _load(parent.data).get("anchor", {}).get("turnId", "") != turn_id:
        raise ValueError("部件轮次与所属消息不一致")
    if kind == "tool_call":
        old = db.get(Part, part_id)
        old_state = _load(old.data).get("state", {}) if old else {}
        status = data.get("status", "pending")
        status = "error" if status in ("failed", "denied") else status
        state = {"status": status, "input": data.get("input") or {}}
        if status == "pending":
            state["raw"] = _dump(state["input"])
        else:
            state["time"] = {"start": old_state.get("time", {}).get("start", now_ms())}
            if status in ("completed", "error"):
                state["time"]["end"] = now_ms()
                if status == "error":
                    state["error"] = data.get("error", data.get("output", "")) or ""
                else:
                    state.update(output=data.get("output") or "", title=data.get("name", ""), metadata={})
        payload = {"type": "tool", "callID": data.get("tool_call_id", part_id),
                   "tool": data.get("name", ""), "state": state}
    else:
        if kind not in ("text", "compaction", "timeline"):
            raise ValueError(f"未实现的 ZCode 部件类型：{kind}")
        payload = {**data, "type": kind}
    row = db.get(Part, part_id)
    if row is None:
        current = db.execute(select(func.max(Part.sequence)).where(Part.message_id == message_id)).scalar()
        row = Part(id=part_id, message_id=message_id, session_id=session_id,
                   sequence=(current if current is not None else -1) + 1, data=_dump(payload))
        db.add(row)
    else:
        if row.message_id != message_id or row.session_id != session_id:
            current = db.scalar(select(func.max(Part.sequence)).where(Part.message_id == message_id))
            row.sequence = (current if current is not None else -1) + 1
            row.message_id, row.session_id = message_id, session_id
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
        row = upsert_message(db, session_id, message_id, "user", {}, turn_id, commit=False)
        # 固定部件 ID 让同一输入的重试幂等；普通助手正文使用独立随机 ID。
        upsert_part(db, session_id, message_id, message_id, "text", {"text": text}, turn_id, commit=False)
        db.commit()
        return row
    except Exception:
        db.rollback()
        raise


def find_last_user_message(db: DbSession, session_id: str) -> Message | None:
    """用户角色从 JSON 查询，仍按稳定 sequence 选择重跑锚点。"""
    return db.execute(select(Message).where(
        Message.session_id == session_id, func.json_extract(Message.data, "$.role") == "user",
        func.coalesce(func.json_extract(Message.data, "$.synthetic"), 0) == 0
    ).order_by(Message.sequence.desc()).limit(1)).scalar()


def rollback_turn(db: DbSession, session_id: str, turn_id: str, from_sequence: int) -> int:
    """保留用户及其正文；级联清理助手部件，再按 JSON 标签删除本轮事实。"""
    result = db.execute(delete(Message).where(
        Message.session_id == session_id,
        func.json_extract(Message.data, "$.role") == "assistant",
        Message.sequence >= from_sequence,
    ))
    db.execute(delete(Message).where(
        Message.session_id == session_id,
        func.json_extract(Message.data, "$.synthetic") == 1,
        func.json_extract(Message.data, "$.anchor.turnId") == turn_id,
    ))
    db.execute(delete(TurnUsage).where(TurnUsage.session_id == session_id, TurnUsage.turn_id == turn_id))
    from app.sessions.approvals import forget_turn
    forget_turn(session_id, turn_id)
    # 重跑恢复先前任务板：从仍保留的成功工具输入重建，不保留已删轮次副作用。
    rows = db.scalars(select(Part).join(Message, Part.message_id == Message.id).where(
        Part.session_id == session_id).order_by(Message.sequence, Part.sequence)).all()
    items = []
    for part in rows:
        data = part_data(part)
        if data.get("name") == "todo_write" and data.get("status") == "completed":
            from app.agent.todo import parse_items
            parsed, error = parse_items(data.get("input", {}).get("items"))
            if not error:
                items = parsed
    replace_todos(db, session_id, items, commit=False)
    db.commit()
    return result.rowcount or 0


def retag_message(db: DbSession, row: Message, label: str) -> None:
    """重跑只改消息轮次标签；其 text part 通过 message_id 自动随之归属。"""
    row.data = _dump(_tag(_load(row.data), label))
    row.time_updated = now_ms()
    db.commit()


def list_turns(db: DbSession, session_id: str) -> list[TurnUsage]:
    """轮次统计与消息内容分开读取。"""
    return list(db.scalars(select(TurnUsage).where(TurnUsage.session_id == session_id).order_by(TurnUsage.started_at)))


def turn_fact(row: TurnUsage) -> dict:
    """仅 API 投影保留前端命名，数据库不保存别名字段。"""
    return {"turn_id": row.turn_id, "state": {"completed": "success", "error": "failed", "cancelled": "stopped"}.get(row.status, row.status),
            "started_at": row.started_at, "ended_at": row.completed_at, "active_ms": row.duration_ms,
            "tokens_used": row.output_tokens, "input_tokens": row.input_tokens,
            "cache_read_tokens": row.cache_read_input_tokens, "cache_creation_tokens": row.cache_creation_input_tokens}


def read_todos(db: DbSession, session_id: str) -> list[dict]:
    return [{"content": r.content, "status": r.status} for r in db.scalars(
        select(Todo).where(Todo.session_id == session_id).order_by(Todo.position))]


def replace_todos(db: DbSession, session_id: str, items: list[dict], *, commit=True) -> None:
    """同一事务全量替换，与 ZCode 按 position 保存当前任务板一致。"""
    try:
        db.execute(delete(Todo).where(Todo.session_id == session_id))
        now = now_ms()
        db.add_all([Todo(session_id=session_id, position=i, content=t["content"], status=t["status"],
                         time_created=now, time_updated=now) for i, t in enumerate(items)])
        touch_session(db, session_id)
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise


def session_info(db: DbSession, row: Session) -> dict:
    """当前模型只是末条实际助手消息的来源，不控制下一次调用。"""
    messages = db.scalars(select(Message).where(Message.session_id == row.id).order_by(Message.sequence)).all()
    model = next((_load(m.data).get("modelId", "") for m in reversed(messages)
                  if message_role(m) == "assistant" and _load(m.data).get("modelId")), "")
    turns = list_turns(db, row.id)
    return {"id": row.id, "title": row.title, "model": model,
            "created_at": row.time_created, "updated_at": row.time_updated,
            "tokens_used": sum(t.output_tokens for t in turns), "input_tokens": sum(t.input_tokens for t in turns)}


def save_compaction(db, session_id, label, before_sequence, summary, tokens_before, tokens_after):
    """摘要是隐藏的合成用户消息；边界存消息 ID，不把 sequence 冒充 ID。"""
    boundary = db.scalar(select(Message).where(Message.session_id == session_id,
        Message.sequence <= before_sequence).order_by(Message.sequence.desc()).limit(1))
    mid = new_id()
    try:
        upsert_message(db, session_id, mid, "user", {
            "synthetic": True, "summary": {"title": "Compact summary", "body": summary},
            "semantics": {"origin": "agent_runtime", "kind": "compact_summary", "uiVisibility": "hidden",
                          "providerVisibility": "visible", "transcriptVisibility": "hidden"}}, label, commit=False)
        upsert_part(db, session_id, mid, new_id(), "text", {"text": summary, "synthetic": True}, label, commit=False)
        upsert_part(db, session_id, mid, new_id(), "compaction", {
            "auto": True, "tail_start_id": boundary.id if boundary else None,
            "preCompactTokenCount": tokens_before, "postCompactTokenCount": tokens_after}, label, commit=False)
        db.commit()
        return mid
    except Exception:
        db.rollback()
        raise
