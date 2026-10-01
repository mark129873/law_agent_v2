"""subtask 子助手：mini_harness §7 的移植。

行为约定（与 mini_harness 一致）：
- 全新消息历史跑独立循环（最多 30 轮），只有 6 个基础工具（无 subtask 防递归）；
- 最终文本作为工具结果返回父级；
- Web 版差异：子循环的文本增量实时以 subtask_delta 事件推给前端右侧面板；
  落盘只保留"目标+状态+最终输出"一张 subtask 卡（子循环内部步骤不落库）；
- token 口径：子循环每次调用的 usage 与主循环同口径处理——累计进会话
  tokens_used、发 token_count 事件、写入 model-io JSONL（带 subtask_id）。

权限差异：子助手中 deny-list 仍然生效（返回 Error 字符串），高危操作
自动放行（交互审批只存在于主循环——子助手是父任务委派的执行细节）。
"""

import asyncio
import logging
import time

from app.agent import permissions
from app.agent.tools import TOOL_HANDLERS, TOOLS, execute_tool
from app.modelio import record_llm_call

logger = logging.getLogger(__name__)

SUBTASK_MAX_TURNS = 30

# 子助手可用工具 = 同步基础工具（排除 subtask/todo_write 等需要主循环上下文的）
SUBTASK_TOOLS = [t for t in TOOLS if t["name"] in TOOL_HANDLERS]

SUBTASK_SYSTEM_PROMPT = (
    "你是子助手，负责完成主助手委派的一个目标。规则：\n"
    "1. 直接执行，不要向用户提问（你面对的不是用户，是主助手）；\n"
    "2. 所有路径都在工作区沙箱内；\n"
    "3. 工具报错时阅读 Error 修正重试；\n"
    "4. 完成后用中文简要汇报结果。"
)


async def run_subtask_events(deps, message_id: str, tool_input: dict):
    """异步生成器：吐 subtask_started/delta/completed 事件，
    最后吐一个 {"_subtask_output": 输出文本} 哨兵（由主循环取走作为工具结果）。
    """
    from app.models import new_id

    goal = str(tool_input.get("goal", "")).strip()
    if not goal:
        yield {"_subtask_output": "Error: subtask 缺少 goal 参数"}
        return

    subtask_id = new_id()
    # 落 running 态卡片，拿回 part id 供收口时更新同一行
    part_id = deps.recorder.write_subtask_part(message_id, {"goal": goal, "status": "running", "output": ""})
    await deps.queue.put({"type": "subtask_started", "subtask_id": subtask_id, "goal": goal})

    child_history: list[dict] = [{"role": "user", "content": goal}]
    output_acc: list[str] = []
    status = "completed"

    try:
        for _turn in range(SUBTASK_MAX_TURNS):
            if deps.stop_flag():
                status = "stopped"
                break
            text_acc = ""
            tool_uses: list[dict] = []
            step_usage: dict = {"input_tokens": 0, "output_tokens": 0}
            child_snapshot = [dict(m) for m in child_history]  # 观测上报用的调用时快照
            step_started = time.monotonic()  # LLM 调用耗时（model-io 记录用）
            llm_error: str | None = None  # 非 None = 该次调用失败（model-io 记录用）
            try:
                async for event in deps.client.stream(
                    system=SUBTASK_SYSTEM_PROMPT, messages=child_history, tools=SUBTASK_TOOLS
                ):
                    kind = event.get("type")
                    if kind == "text_delta":
                        text_acc += event.get("text", "")
                    elif kind == "tool_use":
                        tool_uses.append(event)
                    elif kind == "usage":
                        # 子循环 token 与主循环同口径：计入会话统计（add_tokens），
                        # 并发 token_count 事件给前端（经生成器逐层转发出 SSE）
                        step_usage = {
                            "input_tokens": event.get("input_tokens", 0),
                            "output_tokens": event.get("output_tokens", 0),
                        }
                        deps.recorder.add_tokens(step_usage["output_tokens"])
                        yield {"type": "token_count", **step_usage}
            except Exception as exc:
                llm_error = str(exc)  # 记入 model-io（该次调用失败），再交给外层统一处理
                raise
            finally:
                # model-io：子循环调用也逐次落 JSONL，metadata 带 subtask_id
                record_llm_call(
                    deps.recorder.session_id, deps.recorder.turn_id,
                    getattr(deps.client, "model", "unknown"),
                    SUBTASK_SYSTEM_PROMPT, child_snapshot,
                    response_text=text_acc,
                    tool_calls=[
                        {"id": t["id"], "name": t["name"], "input": t.get("input") or {}}
                        for t in tool_uses
                    ],
                    usage=step_usage,
                    duration_ms=(time.monotonic() - step_started) * 1000,
                    tool_names=[t["name"] for t in SUBTASK_TOOLS],
                    subtask_id=subtask_id,
                    error=llm_error,
                )

            if text_acc:
                output_acc.append(text_acc)
                await deps.queue.put(
                    {"type": "subtask_delta", "subtask_id": subtask_id, "text": text_acc}
                )
            if not tool_uses:
                break

            child_history.append(
                {
                    "role": "assistant",
                    "content": [
                        *( [{"type": "text", "text": text_acc}] if text_acc else [] ),
                        *[
                            {"type": "tool_use", "id": t["id"], "name": t["name"],
                             "input": t.get("input") or {}}
                            for t in tool_uses
                        ],
                    ],
                }
            )
            tool_results = []
            for t in tool_uses:
                if deps.stop_flag():
                    status = "stopped"
                    break
                # deny-list 照样生效；高危自动放行（见模块注释）
                decision, reason = permissions.check(t["name"], t.get("input") or {})
                if decision == "deny":
                    out = f"Error: 已被安全策略拒绝：{reason}"
                else:
                    out = await asyncio.to_thread(
                        execute_tool, t["name"], t.get("input") or {}
                    )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": t["id"], "content": out}
                )
            if status == "stopped":
                break
            child_history.append({"role": "user", "content": tool_results})
        else:
            output_acc.append(f"\n[达到子助手轮数上限 {SUBTASK_MAX_TURNS}]")
    except Exception as exc:
        logger.exception("subtask 执行异常")
        status = "failed"
        output_acc.append(f"\nError: {exc}")

    output = "".join(output_acc).strip() or "(子助手无输出)"
    deps.recorder.update_subtask_part(part_id, message_id, {"goal": goal, "status": status, "output": output})
    await deps.queue.put({"type": "subtask_completed", "subtask_id": subtask_id, "status": status})
    yield {"_subtask_output": output}
