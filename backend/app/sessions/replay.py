"""会话回放层：从四表读出并拼装成上层需要的结构。

两个出口（docs/ARCHITECTURE.md §3.5）：
- load_replay：给前端的完整回放（按 turn 分组：用户消息 → 工作块条目 → 最终回复）；
- load_history：给 agent 的模型可见历史（Anthropic messages 形态，含工具块与
  compact 边界裁剪）。

归属规则（ZCode 同款思想的落地）：
- message 元信息带 metadata.turnId 标签，part 通过 message_id 归属，turn 只是行的标签不是容器；
- 每轮"最后一条 text part"提升为 final_text（工作块外的最终回复），
  其余过程条目全部留在工作块内；
- 没有 turn 收口事实的轮次：在 running_turn_ids 里视为 running，
  否则视为 stopped（服务重启/崩溃的孤儿轮）。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import settings
from app.models import Message, Part, Session
from app.sessions.approvals import build_options
from app.sessions.store import _load, entry_data, list_entries, message_role, turn_id as message_turn_id, part_kind, part_data, session_info


def _message_text(parts: list[Part]) -> str:
    """用户和助手使用相同正文来源；多 text part 按 sequence 拼接。"""
    return "".join(_load(p.data).get("text", "") for p in parts if part_kind(p) == "text")


def _part_data(row: Part) -> dict:
    return part_data(row)


def _load_messages_and_parts(db: DbSession, session_id: str) -> tuple[list[Message], dict[str, list[Part]]]:
    """共用有序查询；前端回放与模型历史各自转换，避免混入不同裁剪规则。"""
    messages = list(
        db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.sequence.asc())
        ).scalars()
    )
    parts_by_message: dict[str, list[Part]] = {}
    if messages:
        stmt = (
            select(Part)
            .where(Part.session_id == session_id, Part.message_id.in_([m.id for m in messages]))
            .order_by(Part.sequence.asc())
        )
        for part in db.execute(stmt).scalars():
            parts_by_message.setdefault(part.message_id, []).append(part)
    return messages, parts_by_message


def load_replay(
    db: DbSession, session_id: str, running_turn_ids: set[str] | None = None
) -> dict | None:
    """拼装前端回放结构；会话不存在或已软删返回 None。

    running_turn_ids：当前真正在跑的 turn 集合（由 turn_manager 提供）。
    为什么需要它：turn 收口事实只在结束时写入，"存储里没有事实"既可能是
    正在跑，也可能是服务中断的孤儿轮，两者前端展示不同（工作中 vs 已停止）。
    """
    running_turn_ids = running_turn_ids or set()

    session_row = db.get(Session, session_id)
    if session_row is None or session_row.time_archived is not None:
        return None

    # --- 事实行 ---
    turn_facts: dict[str, dict] = {}
    approvals: list[dict] = []
    compactions: list[dict] = []
    context_used = 0  # 最近一轮"最后一步 input"≈当前上下文占用（进度条口径）
    for entry in list_entries(db, session_id):
        data = entry_data(entry)
        # 轮次来自 JSON 元信息，由存储适配器投影为回放标签
        turn_label = data.get("turn_id", "")
        if entry.type == "turn":
            turn_facts[data.get("turn_id", "")] = data
            context_used = int(data.get("context_tokens", 0) or 0)  # 时间升序，后者覆盖
        elif entry.type == "approval":
            approvals.append({**data, "turn_id": turn_label})
        elif entry.type == "compaction":
            compactions.append({**data, "turn_id": turn_label})

    # --- 消息与部件 ---
    messages, parts_by_message = _load_messages_and_parts(db, session_id)

    # --- 按 turn 分组（保持 message sequence 顺序即时间线顺序） ---
    turns: dict[str, dict] = {}
    turn_order: list[str] = []
    for msg in messages:
        turn_id = message_turn_id(msg)
        if turn_id not in turns:
            fact = turn_facts.get(turn_id, {})
            turns[turn_id] = {
                "turn_id": turn_id,
                "state": fact.get(
                    "state",
                    "running" if turn_id in running_turn_ids else "stopped",
                ),
                "started_at": fact.get("started_at", msg.time_created),
                "ended_at": fact.get("ended_at"),
                "active_ms": fact.get("active_ms"),
                "user_message": None,
                "work_items": [],
                "final_text": None,
                "_text_parts": [],  # 内部暂存，最后一条提升为 final_text
                "_first_seq": msg.sequence,
            }
            turn_order.append(turn_id)

        bucket = turns[turn_id]
        if message_role(msg) == "user" and bucket["user_message"] is None:
            bucket["user_message"] = {
                "id": msg.id,
                "text": _message_text(parts_by_message.get(msg.id, [])),
                "time": msg.time_created,
            }
            continue  # 用户消息不入工作块

        # assistant 消息：把 parts 摊平成工作条目
        for part in parts_by_message.get(msg.id, []):
            data = _part_data(part)
            item_time = part.time_created
            if part_kind(part) == "text":
                bucket["_text_parts"].append(data.get("text", ""))
                bucket["_last_text_id"] = part.id
                bucket["work_items"].append(
                    {"kind": "text", "id": part.id, "text": data.get("text", ""), "time": item_time}
                )
            elif part_kind(part) == "tool_call":
                bucket["work_items"].append(
                    {
                        "kind": "tool_call",
                        "id": part.id,
                        "name": data.get("name", ""),
                        "input": data.get("input"),
                        "status": data.get("status", "completed"),
                        "output": data.get("output", ""),
                        "time": item_time,
                    }
                )
            elif part_kind(part) == "subtask":
                bucket["work_items"].append(
                    {
                        "kind": "subtask",
                        "id": part.id,
                        "goal": data.get("goal", ""),
                        "status": data.get("status", "completed"),
                        "output": data.get("output", ""),
                        "time": item_time,
                    }
                )
            elif part_kind(part) == "todo":
                bucket["work_items"].append(
                    {"kind": "todo", "id": part.id, "items": data.get("items", []), "time": item_time}
                )
            elif part_kind(part) == "error":
                bucket["work_items"].append(
                    {"kind": "error", "id": part.id, "message": data.get("message", ""), "time": item_time}
                )

    # 审批与压缩事实按 turn_id 归入工作块。
    # 审批同一 request_id 只保留最新一条事实（请求→决定是同一张卡的演进，
    # 全量渲染会出现"等待批准"幽灵卡；未决判定也用同一份最新状态）
    latest_by_request: dict[str, dict] = {}
    for data in approvals:
        latest_by_request[data.get("request_id", "")] = data
    for data in latest_by_request.values():
        bucket = turns.get(data.get("turn_id", ""))
        if bucket is not None:
            bucket["work_items"].append(
                {
                    "kind": "approval",
                    "id": data.get("request_id", ""),
                    "tool": data.get("tool", ""),
                    "input": data.get("input"),
                    "reason": data.get("reason", ""),
                    "status": data.get("status", "requested"),
                    "time": data.get("time", 0),
                }
            )
    for data in compactions:
        bucket = turns.get(data.get("turn_id", ""))
        if bucket is not None:
            bucket["work_items"].append(
                {
                    "kind": "compaction",
                    "id": data.get("time", 0),
                    "tokens_before": data.get("tokens_before"),
                    "tokens_after": data.get("tokens_after"),
                    "time": data.get("time", 0),
                }
            )

    # --- 收尾：每轮最后一条 text 提升为 final_text（ZCode 归属切分） ---
    result_turns = []
    for turn_id in turn_order:
        bucket = turns[turn_id]
        text_parts = bucket.pop("_text_parts")
        last_text_id = bucket.pop("_last_text_id", None)
        final_text = text_parts[-1] if text_parts else None
        items = bucket["work_items"]
        # 末条 text 已提升为块外最终回复，从块内条目中剔除（按记录的 part id 精确剔除）
        if final_text is not None and last_text_id:
            items = [x for x in items if not (x["kind"] == "text" and x["id"] == last_text_id)]
        bucket["final_text"] = final_text
        bucket["work_items"] = sorted(items, key=lambda x: x.get("time", 0))
        result_turns.append(bucket)

    # 未决审批：用上面去重后的最新状态（仍是 requested 即未决）。
    # 附上动态选项与 full_access 标志——刷新恢复的弹窗与实时流同构。
    pending = next(
        (a for a in latest_by_request.values() if a.get("status") == "requested"), None
    )
    if pending is not None:
        pending = {
            **pending,
            "options": build_options(pending.get("tool", ""), pending.get("input") or {}, True),
            "full_access": True,
        }

    return {
        "session": {
            **session_info(db, session_row),
            "context_used": context_used,
            "context_window": settings.context_window,
        },
        "turns": result_turns,
        "pending_approval": pending,
    }


def load_history(db: DbSession, session_id: str) -> list[dict]:
    """重建模型可见历史（Anthropic messages 形态）。

    规则：
    - 有 compact 事实时，只取 before_sequence 之后的消息，并在最前面放一条
      携带摘要的 user 消息（ZCode：摘要消息成为边界）；
    - assistant 消息 = text 块 + tool_use 块（每个 tool_call part 一块，
      无论结果状态如何——模型必须看到自己发起的调用与结果才能续推）；
    - assistant 消息带 tool_use 时，紧跟一条合成的 user 消息承载 tool_result 块。
    """
    messages, parts_by_message = _load_messages_and_parts(db, session_id)

    # compact 边界：取最后一次压缩的 before_sequence 与摘要文本
    cutoff_seq = 0
    summary_text = ""
    for entry in list_entries(db, session_id, "compaction"):
        data = entry_data(entry)
        cutoff_seq = max(cutoff_seq, data.get("before_sequence", 0))
        summary_text = data.get("summary_text", summary_text)

    result: list[dict] = []
    if summary_text:
        result.append(
            {
                "role": "user",
                "content": f"<此前对话摘要>\n{summary_text}\n</此前对话摘要>",
            }
        )

    pending_tool_results: list[dict] = []  # 待并入下一条 user 消息的 tool_result 块

    def flush_tool_results() -> None:
        nonlocal pending_tool_results
        if pending_tool_results:
            result.append({"role": "user", "content": pending_tool_results})
            pending_tool_results = []

    for msg in messages:
        if msg.sequence <= cutoff_seq:
            continue
        if message_role(msg) == "user":
            flush_tool_results()
            text = _message_text(parts_by_message.get(msg.id, []))
            result.append({"role": "user", "content": text})
            continue

        # assistant 消息：先把上一步的 tool_result 补成 user 消息（API 要求
        # tool_use 后必须紧跟 tool_result），再落本条 assistant
        flush_tool_results()
        content: list[dict] = []
        for part in parts_by_message.get(msg.id, []):
            data = _part_data(part)
            if part_kind(part) == "text" and data.get("text"):
                content.append({"type": "text", "text": data["text"]})
            elif part_kind(part) == "tool_call":
                content.append(
                    {
                        "type": "tool_use",
                        "id": data.get("tool_call_id", part.id),
                        "name": data.get("name", ""),
                        "input": data.get("input") or {},
                    }
                )
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": data.get("tool_call_id", part.id),
                        "content": data.get("output", ""),
                    }
                )
        if content:
            result.append({"role": "assistant", "content": content})

    flush_tool_results()
    return result
