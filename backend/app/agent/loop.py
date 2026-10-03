"""agent 主循环：mini_harness §10 的移植，形态改为异步事件生成器。

一个 turn = 一次 run_turn() 调用：
  用户输入 → [压缩检查(BE-7)] → LLM 流式调用 → 有 tool_use 则
  过权限闸门 → 审批回调(BE-6 接交互) → 线程池执行 → 结果回填续轮；
  无 tool_use 且 Stop hook 无注入则收口。

设计约定：
- 事件即产物：循环是 async generator，逐个 yield 事件 dict（SSE 层直接转发）；
- 错误不打断循环：工具异常由 execute_tool 转成 Error 字符串（tools.py），
  LLM 异常转成 error 事件并以 failed 收口；
- stop_flag 在每个检查点轮询：流中、每个工具执行前、每轮开始；
  命中即以 stopped 收口，已生成的部分已落盘（停止后保留部分输出）；
- 工时记账：审批等待前后调 recorder.pause/resume，active_ms 不含等待。
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from app.agent import hooks
from app.agent.permission_service import evaluate
from app.agent.tools import TOOLS, execute_tool
from app.modelio import record_llm_call
from app.sessions.recorder import TurnRecorder

logger = logging.getLogger(__name__)

# 单 turn 的模型步上限（安全阀；mini_harness 子循环 30 轮同思想）
MAX_STEPS_DEFAULT = 40

# 输出预览长度：SSE 事件里只带预览，全文走回放接口
_PREVIEW_LEN = 400


async def _auto_approver(tool_name: str, tool_input: dict, reason: str, **_kwargs) -> dict:
    """默认审批回调：BE-4 阶段自动批准（BE-6 的交互审批经 deps 注入替换）。

    为什么默认放行：验证循环与权限分档逻辑本身；返回 dict 与
    InteractiveApprover 的契约一致（approved + denial_reason）。
    """
    return {"approved": True, "denial_reason": None}


@dataclass
class TurnDeps:
    """一次 turn 的全部依赖（测试可全部注入假实现）。

    settings 为 None 时禁用压缩检查（单元测试默认禁用，真实端点传入）。
    """

    client: object  # LLM 流式客户端：async stream(system, messages, tools) -> async iterator
    recorder: TurnRecorder
    system_prompt: str
    history: list = field(default_factory=list)  # Anthropic messages 形态，原地追加
    tools: list = field(default_factory=lambda: list(TOOLS))
    approver: Callable = _auto_approver  # async (tool, input, reason) -> bool
    max_steps: int = MAX_STEPS_DEFAULT
    stop_flag: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    settings: object = None  # 压缩预算等（None = 禁用压缩）
    last_input_tokens: int | None = None  # 上一轮真实 input_tokens（无则估算）
    queue: object | None = None  # SSE 事件队列（subtask/todo_write 直推事件用）
    persist_user_message: bool = True  # 重新生成时为 False（用户消息已存在）
    user_sequence: int | None = None  # 重新生成时锚定的既有用户消息 sequence


def _preview(text: str) -> str:
    """工具输出的事件预览（全文走回放接口）。"""
    return text if len(text) <= _PREVIEW_LEN else text[:_PREVIEW_LEN] + "…"


def _is_error_output(output: str) -> bool:
    """mini_harness 约定：Error 开头的输出代表工具执行失败。"""
    return isinstance(output, str) and output.startswith("Error:")


def _part_id_for(tool_call_id: str) -> str:
    """工具部件的稳定行 id：同一次调用的多次状态推进写同一行。"""
    return f"tl{uuid.uuid4().hex[:30]}"  # 32 字符以内


def _last_user_text(history: list) -> str:
    """取用户输入文本（历史最后一条 user 消息由 BE-5 端点压入）。"""
    for msg in reversed(history):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


def _assistant_blocks(text: str, tool_uses: list[dict]) -> list[dict]:
    """构造回填历史的 assistant 内容块（text + tool_use）。"""
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for tool_use in tool_uses:
        blocks.append(
            {
                "type": "tool_use",
                "id": tool_use["id"],
                "name": tool_use["name"],
                "input": tool_use.get("input") or {},
            }
        )
    return blocks


async def run_turn(deps: TurnDeps) -> AsyncIterator[dict]:
    """执行一个回复轮次，逐个吐事件。最终事件必为 turn_completed。"""
    turn_id = deps.recorder.begin_turn(
        user_text=_last_user_text(deps.history),
        persist_user=deps.persist_user_message,
        user_sequence=deps.user_sequence,
    )
    logger.info("turn 开始 session=%s turn=%s", deps.recorder.session_id, turn_id)
    yield {"type": "turn_started", "turn_id": turn_id, "started_at": deps.recorder.started_at}

    state = "success"
    stop_requested = False

    try:
        # ---- 压缩检查（仅 turn 开始时；未配置 settings 则跳过） ----
        if deps.settings is not None:
            from app.agent.compact import maybe_compact

            async for compact_event in maybe_compact(deps):
                yield compact_event

        for _step in range(deps.max_steps):
            # ---- 检查点：停止 ----
            if deps.stop_flag():
                stop_requested = True
                break

            # ---- LLM 流式调用（一个模型步） ----
            message_id = deps.recorder.step_message()
            text_acc = ""
            tool_uses: list[dict] = []
            step_usage = {"input_tokens": 0, "output_tokens": 0}
            messages_snapshot = [dict(m) for m in deps.history]  # 观测上报用的调用时快照
            step_started = time.monotonic()  # LLM 调用耗时（model-io 记录用）
            llm_error: str | None = None  # 非 None = 该次调用失败（model-io 记录用）
            try:
                async for event in deps.client.stream(
                    system=deps.system_prompt, messages=deps.history, tools=deps.tools
                ):
                    if deps.stop_flag():
                        stop_requested = True
                        break
                    kind = event.get("type")
                    if kind == "text_delta":
                        text_acc += event.get("text", "")
                        yield {"type": "delta", "text": event.get("text", "")}
                    elif kind == "tool_use":
                        tool_uses.append(event)
                    elif kind == "usage":
                        step_usage = {
                            "input_tokens": event.get("input_tokens", 0),
                            "output_tokens": event.get("output_tokens", 0),
                            "cache_read_tokens": event.get("cache_read_tokens", 0),
                            "cache_creation_tokens": event.get("cache_creation_tokens", 0),
                        }
                        deps.recorder.add_usage(
                            step_usage["input_tokens"], step_usage["output_tokens"],
                            cache_read_tokens=step_usage["cache_read_tokens"],
                            cache_creation_tokens=step_usage["cache_creation_tokens"],
                        )
                        # 真实 input 喂给 compact 预算（此前该字段无人赋值，只能字符估算）
                        deps.last_input_tokens = step_usage["input_tokens"]
                        yield {
                            "type": "token_count",
                            "input_tokens": event.get("input_tokens", 0),
                            "output_tokens": event.get("output_tokens", 0),
                        }
            except Exception as exc:  # LLM 层异常：转 error 事件，failed 收口
                logger.exception("LLM 调用失败")
                llm_error = str(exc)  # 记入 model-io（该次调用失败）
                deps.recorder.write_error_part(message_id, str(exc))
                yield {"type": "error", "message": str(exc)}
                state = "failed"
                break
            finally:
                # model-io：逐调用快照落 JSONL（成功与失败都记；失败不影响主流程）
                record_llm_call(
                    deps.recorder.session_id, turn_id,
                    getattr(deps.client, "model", "unknown"),
                    deps.system_prompt, messages_snapshot,
                    response_text=text_acc,
                    tool_calls=[
                        {"id": t["id"], "name": t["name"], "input": t.get("input") or {}}
                        for t in tool_uses
                    ],
                    usage=step_usage,
                    duration_ms=(time.monotonic() - step_started) * 1000,
                    tool_names=[t["name"] for t in deps.tools],
                    error=llm_error,
                )

            # ---- 里程碑落盘：正文与工具调用（pending 态） ----
            if text_acc:
                deps.recorder.write_text_part(message_id, text_acc)
            part_ids: dict[str, str] = {}
            for tool_use in tool_uses:
                part_id = _part_id_for(tool_use["id"])
                part_ids[tool_use["id"]] = part_id
                deps.recorder.upsert_tool_part(
                    message_id,
                    part_id,
                    {
                        "tool_call_id": tool_use["id"],
                        "name": tool_use["name"],
                        "input": tool_use.get("input") or {},
                        "status": "pending",
                    },
                )

            if stop_requested:
                # 流中途被停：已生成的部分已落盘，以 stopped 收口
                break

            # ---- 无工具调用：本轮结束（Stop hook 可注入内容强制续轮） ----
            if not tool_uses:
                deps.history.append({"role": "assistant", "content": text_acc})
                injection = hooks.trigger("Stop", {"turn_id": turn_id, "steps": _step + 1})
                if injection:
                    deps.history.append({"role": "user", "content": injection})
                    continue
                break

            # ---- 工具执行：权限闸门 → 审批 → 线程池执行 ----
            deps.history.append(
                {"role": "assistant", "content": _assistant_blocks(text_acc, tool_uses)}
            )
            tool_results: list[dict] = []
            for tool_use in tool_uses:
                if deps.stop_flag():
                    stop_requested = True
                    break
                result_box: dict = {}
                async for event in _execute_one(deps, message_id, tool_use, part_ids[tool_use["id"]], result_box):
                    yield event
                tool_results.append(result_box["tool_result"])

            if stop_requested:
                break
            if tool_results:
                deps.history.append({"role": "user", "content": tool_results})
        else:
            # 步数用尽（安全阀）
            yield {"type": "error", "message": f"达到单轮步数上限 {deps.max_steps}"}
            state = "failed"
    except Exception as exc:  # 循环级兜底：任何意外都不允许吞掉收口事件
        logger.exception("turn 循环异常")
        yield {"type": "error", "message": str(exc)}
        state = "failed"

    if stop_requested:
        state = "stopped"
    fact = deps.recorder.end_turn(state)
    logger.info(
        "turn 收口 session=%s turn=%s state=%s 工时=%.0fms tokens=%s",
        deps.recorder.session_id, turn_id, state, fact["active_ms"], fact["tokens_used"],
    )
    yield {
        "type": "turn_completed",
        "turn_id": turn_id,
        "ended_at": fact["ended_at"],
        "active_ms": fact["active_ms"],
        "state": state,
    }


async def _execute_one(
    deps: TurnDeps, message_id: str, tool_use: dict, part_id: str, result_box: dict
) -> AsyncIterator[dict]:
    """单个工具调用：权限分档 → 审批 → 执行 → 落盘 + 事件（异步生成器）。

    为什么是生成器：subtask/todo_write 需要在执行过程中实时吐事件
    （子助手流式输出、任务板更新），不能等执行完一次性返回。
    结果通过 result_box["tool_result"] 带出（生成器无法用 return 值传给调用方）。
    约定：denied（策略拒绝/用户拒绝）不执行、不发 tool_started，只发结果卡。
    """
    name = tool_use["name"]
    tool_input = tool_use.get("input") or {}
    call_id = tool_use["id"]
    started = time.time()

    hooks.trigger("PreToolUse", {"name": name, "tool_call_id": call_id})

    # 权限评估（ZCode checkPermission 复刻）：allow 执行 / deny 拒绝 / ask 走审批
    result = evaluate(deps.settings.data_dir if deps.settings else None, name, tool_input)
    decision, reason = result["decision"], result["reason"]
    status = "completed"
    output: str | None = None

    if decision == "deny":
        output = f"Error: 已被安全策略拒绝：{reason}"
        status = "denied"
    else:
        if decision == "ask":
            # 审批等待不计入有效工时（active_ms 口径）
            deps.recorder.pause_active()
            try:
                outcome = await deps.approver(name, tool_input, reason or "该操作需要确认")
            finally:
                deps.recorder.resume_active()
            approved = outcome["approved"]
            if not approved:
                output = f"Error: 用户拒绝了该操作：{outcome['denial_reason'] or reason}"
                status = "denied"

        if output is None:
            deps.recorder.upsert_tool_part(
                message_id, part_id,
                {"tool_call_id": call_id, "name": name, "input": tool_input, "status": "running"},
            )
            if name == "subtask":
                # 特殊工具：嵌套循环，事件实时吐（最后由哨兵带出输出文本）
                from app.agent.subtask import run_subtask_events

                async for event in run_subtask_events(deps, message_id, tool_input):
                    if "_subtask_output" in event:
                        output = event["_subtask_output"]
                    else:
                        yield event
            elif name == "todo_write":
                # 特殊工具：落 todo part + todo_updated 事件
                from app.agent.todo import handle_todo_write

                output, extra_events = await handle_todo_write(deps, message_id, tool_input)
                for event in extra_events:
                    yield event
            else:
                output = await asyncio.to_thread(execute_tool, name, tool_input)
            if _is_error_output(output):
                status = "failed"

    duration_ms = (time.time() - started) * 1000
    deps.recorder.upsert_tool_part(
        message_id, part_id,
        {
            "tool_call_id": call_id, "name": name, "input": tool_input,
            "status": status, "output": output, "duration_ms": duration_ms,
        },
    )
    hooks.trigger(
        "PostToolUse",
        {"name": name, "tool_call_id": call_id, "status": status, "duration_ms": duration_ms},
    )

    if status in ("completed", "failed"):
        yield {"type": "tool_started", "tool_call_id": call_id, "name": name, "input": tool_input}
    yield {
        "type": "tool_completed",
        "tool_call_id": call_id,
        "name": name,
        "status": status,
        "output_preview": _preview(output or ""),
    }
    result_box["tool_result"] = {"type": "tool_result", "tool_use_id": call_id, "content": output or ""}
