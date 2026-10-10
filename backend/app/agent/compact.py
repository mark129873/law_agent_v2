"""上下文压缩：ZCode compact 思路的移植（docs/ARCHITECTURE.md §5）。

策略（从便宜到贵）：
1. 不动：估算/真实 token 未超预算 → 什么都不做；
2. microcompact：本地把旧工具结果改写为占位符（保留最近 N 条完整），
   零 LLM 成本，先做；
3. compact：仍超预算才调 LLM 生成结构化摘要，摘要成为 resume 边界
   （load_history 只发边界之后的消息）。

预算口径（产品决策）：预算 = 上下文窗口 − 32K 输出预留 − 13K buffer。
token 来源：优先用上一次 API 调用的真实 input_tokens，否则按字符数/4 估算
（mini_harness 的混合估算思想）。

熔断：连续 3 次摘要失败后本会话跳过 compact（避免每轮都烧一次失败调用），
回退到反应式压缩（API 报 prompt_too_long 时由调用方处理）。

为什么只在 turn 开始时压缩：一轮之内增长有限，turn 边界是天然的干净
切分点；轮内超长由反应式重试兜底。
"""

import asyncio
import logging
from typing import AsyncIterator

from sqlalchemy import func, select

from app.models import Part
from app.sessions import store

logger = logging.getLogger(__name__)

# 连续摘要失败熔断阈值（ZCode policy 同款数字）
_MAX_COMPACT_FAILURES = 3
# 占位符文本（microcompact 改写旧工具输出用）
_MICRO_PLACEHOLDER = "[旧工具结果已清除以节省上下文]"

# 会话 -> 连续失败计数（进程内；重启清零无妨）
_failure_counts: dict[str, int] = {}


def context_budget(settings) -> int:
    """可用上下文预算 = 窗口 − 输出预留 − 安全缓冲。"""
    return settings.context_window - settings.compact_output_reserve - settings.compact_buffer


def estimate_tokens(text: str) -> int:
    """字符数/4 粗估（与 mini_harness 一致；仅用于无真实 usage 时的兜底）。"""
    return max(1, len(text) // 4)


def estimate_history_tokens(history: list) -> int:
    """估算整段历史的 token（Anthropic messages 形态）。"""
    total = 0
    for msg in history:
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content) + 4
        elif isinstance(content, list):
            for block in content:
                total += estimate_tokens(json_dumps_safe(block)) + 4
    return total


def json_dumps_safe(obj) -> str:
    import json

    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)


def needs_compact(settings, history: list, last_input_tokens: int | None = None) -> bool:
    """是否需要压缩：真实 usage 优先，估算兜底。"""
    estimate = (
        last_input_tokens
        if last_input_tokens is not None and last_input_tokens > 0
        else estimate_history_tokens(history)
    )
    return estimate > context_budget(settings)


def microcompact_parts(db, session_id: str, keep: int) -> int:
    """把较旧的工具输出改写为占位符，保留最近 keep 条完整。

    只动"已完成/已失败"且输出非空的 tool_call part；返回改写条数。
    为什么整行 upsert：与存储层里程碑语义一致，sequence/时间线不动。
    """
    parts = list(
        db.execute(
            select(Part)
            .where(Part.session_id == session_id, func.json_extract(Part.data, "$.type") == "tool")
            .order_by(Part.time_updated.asc())
        ).scalars()
    )
    candidates = [p for p in parts if _has_output(p)]
    to_clear = candidates[:-keep] if len(candidates) > keep else []
    changed = 0
    for part in to_clear:
        data = store._load(part.data)
        key = "error" if data["state"]["status"] == "error" else "output"
        data["state"][key] = _MICRO_PLACEHOLDER
        part.data = store._dump(data)
        changed += 1
    if changed:
        db.commit()
    return changed


def _has_output(part: Part) -> bool:
    data = store.part_data(part)
    return bool(data.get("output")) and data.get("output") != _MICRO_PLACEHOLDER


def build_transcript(history: list) -> str:
    """把历史铺成纯文本对话稿（摘要调用的输入）。"""
    lines: list[str] = []
    for msg in history:
        role = "用户" if msg.get("role") == "user" else "助手"
        content = msg.get("content")
        if isinstance(content, str):
            lines.append(f"{role}: {content}")
        elif isinstance(content, list):
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    lines.append(f"{role}: {block.get('text', '')}")
                elif btype == "tool_use":
                    lines.append(f"助手调用工具 {block.get('name')}，参数: {json_dumps_safe(block.get('input'))}")
                elif btype == "tool_result":
                    lines.append(f"工具结果: {block.get('content', '')}")
    return "\n".join(lines)


COMPACT_PROMPT = """你是会话压缩器。把下面这段"助手与用户的对话稿"压缩为结构化摘要，供助手后续继续工作使用。要求：
1. 【当前任务】逐字保留最后一条用户请求原文；
2. 【已完成】关键步骤与结果；
3. 【关键决定】重要方案决定；
4. 【涉及文件】创建/修改过的文件路径；
5. 【待办】未完成事项；
6. 【失败与教训】报错与规避方式；
7. 【用户约束】用户提出的硬性要求，逐字保留。
直接输出摘要正文，不要输出任何解释。"""


async def summarize_history(client, history: list) -> str:
    """调 LLM 生成摘要（复用流式客户端，tools 置空）。"""
    transcript = build_transcript(history)
    chunks: list[str] = []
    async for event in client.stream(
        system=COMPACT_PROMPT,
        messages=[{"role": "user", "content": f"<对话稿>\n{transcript}\n</对话稿>"}],
        tools=[],
    ):
        if event.get("type") == "text_delta":
            chunks.append(event.get("text", ""))
    return "".join(chunks).strip()


async def maybe_compact(deps) -> AsyncIterator[dict]:
    """turn 开始时的压缩检查（异步生成器：边做边吐 compacted 事件）。

    就地改写 deps.history：压缩后变为 [摘要 user 消息, 当前用户消息]。
    熔断中的会话直接跳过（记 WARNING 日志）。
    """
    from app.config import settings

    settings = deps.settings
    history = deps.history
    if len(history) < 2:
        return  # 首轮无事可压

    if not needs_compact(settings, history, deps.last_input_tokens):
        return

    session_id = deps.recorder.session_id

    # ---- 第一级：microcompact（零成本） ----
    cleared = microcompact_parts(deps.recorder.db, session_id, settings.microcompact_keep)
    # 重建历史（工具结果占位要反映到内存历史里）
    fresh = await _reload_history(deps)
    if fresh is not None:
        deps.history[:] = fresh
    if not needs_compact(settings, deps.history, None):
        logger.info("microcompact 后回到预算内 session=%s 清理=%s 条", session_id, cleared)
        return

    # ---- 第二级：LLM 摘要 ----
    if _failure_counts.get(session_id, 0) >= _MAX_COMPACT_FAILURES:
        logger.warning("compact 熔断中，跳过摘要 session=%s", session_id)
        return

    prior = history[:-1]  # 当前用户消息不参与摘要
    if not prior:
        return
    tokens_before = estimate_history_tokens(history)
    try:
        summary_text = await asyncio.wait_for(
            summarize_history(deps.client, prior), timeout=120
        )
    except Exception as exc:
        _failure_counts[session_id] = _failure_counts.get(session_id, 0) + 1
        logger.warning("compact 摘要失败(%s/%s): %s", _failure_counts[session_id], _MAX_COMPACT_FAILURES, exc)
        return

    _failure_counts[session_id] = 0
    cutoff = deps.recorder.first_user_sequence - 1  # 当前轮的消息全部留在边界之后
    store.save_compaction(deps.recorder.db, session_id, deps.recorder.turn_id,
                          cutoff, summary_text, tokens_before, estimate_tokens(summary_text))
    # 就地重建内存历史：摘要 + 当前用户消息
    deps.history[:] = [
        {"role": "user", "content": f"<此前对话摘要>\n{summary_text}\n</此前对话摘要>"},
        history[-1],
    ]
    yield {
        "type": "compacted",
        "tokens_before": tokens_before,
        "tokens_after": estimate_tokens(summary_text),
    }


async def _reload_history(deps) -> list | None:
    """microcompact 后从库重读历史（turn 开始时未产生新行，安全）。"""
    from app.sessions import replay

    try:
        return replay.load_history(deps.recorder.db, deps.recorder.session_id)
    except Exception:
        logger.exception("microcompact 后重读历史失败")
        return None


def reset_failure_count(session_id: str) -> None:
    """（测试辅助）清空指定会话的熔断计数。"""
    _failure_counts.pop(session_id, None)
