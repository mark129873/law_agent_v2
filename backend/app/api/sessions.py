"""会话路由：新建（draft）、列表、详情（全量回放）、删除（软删）、turn（SSE）、停止。

turn 端点的并发模型（为什么用"后台泵任务 + 队列"）：
- SSE 连接断开（用户刷新页面）不应杀死 turn——产品语义是"刷新回看"，
  turn 在后台继续跑完并落盘，用户刷新即可看到最新进度；
- 所以 run_turn 的事件先泵进 asyncio.Queue，SSE 生成器只负责从队列读帧；
  断开时生成器退出，泵任务继续执行直到 turn 收口。
"""

import asyncio
import json
import logging
from pathlib import Path
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from app import db
from app.agent.llm import build_client
from app.agent.loop import TurnDeps, run_turn
from app.config import settings, validate_llm_config
from app.models import new_id
from app.sessions import approvals, execution_state, replay, store, turn_manager
from app.sessions.approvals import InteractiveApprover
from app.sessions.recorder import TurnRecorder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# SSE 心跳间隔（秒）：注释帧保活，防止代理/浏览器掐断空闲连接
_KEEPALIVE_SECONDS = 15

_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "agent" / "system_prompt.md"


class TurnIn(BaseModel):
    """POST /turn 请求体。

    mode：协作模式随提交生效（ZCode resolveSubmittedExecutionState
    语义）；缺省沿用当前执行状态。
    """

    text: str
    mode: str | None = None


def get_llm_client() -> object:
    """LLM 客户端依赖（测试用 dependency_overrides 注入假客户端）。"""
    return build_client(settings)


def _load_system_prompt() -> str:
    """每轮现读系统提示词，让文件修改在下一轮立即生效。"""
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _sse_frame(event: dict) -> str:
    """把事件 dict 编码为 SSE 帧（event 行 + data 行）。"""
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("")
def create_session() -> dict:
    """新建会话（draft 语义）：只发一个 id，不落库。

    为什么不落库：与 ZCode 一致，空会话不值得持久化；首条消息发出时
    由 store.ensure_session 正式建行并生成标题。
    """
    return {"id": new_id()}


@router.get("")
def list_sessions(db_session: DbSession = Depends(db.get_db)) -> list[dict]:
    """会话列表：过滤软删、按更新时间倒序、带 running 标志（侧栏呼吸点轮询）。"""
    items = []
    for row in store.list_sessions(db_session):
        items.append(
            {
                **store.session_info(db_session, row),
                "running": turn_manager.is_running(row.id),
            }
        )
    return items


@router.get("/{session_id}")
def get_session_detail(session_id: str, db_session: DbSession = Depends(db.get_db)) -> dict:
    """会话详情：全量回放（消息、工作块条目、turn 事实、未决审批）。

    这就是 resume 的读取路径——打开旧会话即拿到全部历史。
    """
    result = replay.load_replay(
        db_session, session_id, running_turn_ids=turn_manager.running_turn_ids()
    )
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return result


@router.delete("/{session_id}")
def delete_session(session_id: str, db_session: DbSession = Depends(db.get_db)) -> dict:
    """删除会话（用户视角永久删除，数据库软删标记）。

    进行中的会话不允许删（先停止），返回 409。
    """
    if turn_manager.is_running(session_id):
        raise HTTPException(status_code=409, detail="会话正在生成中，请先停止")
    if not store.soft_delete_session(db_session, session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    approvals.forget_session(session_id)
    return {"ok": True}


@router.post("/{session_id}/turn")
async def start_turn(
    session_id: str,
    body: TurnIn,
    client: object = Depends(get_llm_client),
) -> StreamingResponse:
    """发送用户消息，响应即 SSE 事件流（前端用 fetch ReadableStream 消费）。"""
    _check_llm_config()
    _check_not_running(session_id)

    # 模式随提交生效（ZCode resolveSubmittedExecutionState 语义）：缺省沿用当前
    if body.mode:
        execution_state.save_execution_state(settings.data_dir, body.mode)
    with _prepare_turn(session_id) as (recorder, stop_event):
        history = replay.load_history(recorder.db, session_id)
        history.append({"role": "user", "content": body.text})
        return _turn_sse_response(client, recorder, history, stop_event)


@router.post("/{session_id}/regenerate")
async def regenerate_turn(
    session_id: str,
    client: object = Depends(get_llm_client),
) -> StreamingResponse:
    """重新生成：保留最后一轮的用户消息，回滚其后内容并重跑（错误重试同机制）。"""
    _check_llm_config()
    _check_not_running(session_id)

    with _prepare_turn(session_id) as (recorder, stop_event):
        turn_db = recorder.db
        last_user = store.find_last_user_message(turn_db, session_id)
        if last_user is None:
            raise HTTPException(status_code=404, detail="没有可重新生成的轮次")

        # 回滚仅影响原轮回复和事实；保留的用户消息仍可在装配失败后再次重跑。
        store.rollback_turn(turn_db, session_id, store.turn_id(last_user), last_user.sequence)
        store.retag_message(turn_db, last_user, recorder.turn_id)
        history = replay.load_history(turn_db, session_id)
        return _turn_sse_response(
            client, recorder, history, stop_event, user_sequence=last_user.sequence
        )


@contextmanager
def _prepare_turn(session_id: str):
    """装配期拥有独立Session；成功交给pump，失败/取消则立即释放登记和连接。"""
    turn_db = db.new_session()
    recorder = None
    try:
        recorder = TurnRecorder(turn_db, session_id, model=settings.model_id, max_tokens=settings.max_tokens)
        stop_event = turn_manager.register(session_id, recorder.turn_id)
        yield recorder, stop_event
    except BaseException:
        # close 同时回滚未提交事务；不让失败的装配永久占用同会话单turn槽位。
        try:
            if recorder is not None:
                turn_manager.unregister(session_id, recorder.turn_id)
        finally:
            turn_db.close()
        raise


def _check_llm_config() -> None:
    """发起对话前的 LLM 配置校验（健康检查不依赖它，方便无钥测试）。"""
    missing = validate_llm_config(settings)
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少 LLM 配置：{','.join(missing)}")


def _check_not_running(session_id: str) -> None:
    """同会话单 turn：已在生成中则 409。"""
    if turn_manager.is_running(session_id):
        raise HTTPException(status_code=409, detail="该会话正在生成中")


def _turn_sse_response(
    client, recorder: TurnRecorder, history: list, stop_event: asyncio.Event,
    *, user_sequence: int | None = None,
) -> StreamingResponse:
    """共用turn装配与SSE出口；重跑传用户sequence，避免重复保存请求。"""
    session_id, turn_db = recorder.session_id, recorder.db
    queue: asyncio.Queue = asyncio.Queue()
    # 审批与循环必须共用同一Queue，否则等待时发出的审批事件无人消费。
    deps = TurnDeps(
        client=client,
        recorder=recorder,
        system_prompt=_load_system_prompt(),
        history=history,
        stop_flag=stop_event.is_set,
        approver=InteractiveApprover(turn_db, session_id, recorder, queue, stop_event),
        settings=settings,
        queue=queue,
        persist_user_message=user_sequence is None,
        user_sequence=user_sequence,
    )

    async def pump() -> None:
        """把循环事件泵进队列；收口后注销并关闭独立会话。"""
        try:
            async for event in run_turn(deps):
                await queue.put(event)
        except Exception as exc:  # 泵级兜底：保证前端总能收到收尾信号
            logger.exception("turn 泵异常")
            await queue.put({"type": "error", "message": str(exc)})
            await queue.put(
                {"type": "turn_completed", "turn_id": recorder.turn_id,
                 "ended_at": 0, "active_ms": 0, "state": "failed"}
            )
        finally:
            await queue.put(None)
            turn_manager.unregister(session_id, recorder.turn_id)
            turn_db.close()

    async def event_stream():
        """SSE 生成器：读队列转帧；断开时退出但不杀后台 turn（刷新回看语义）。"""
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if event is None:
                break
            yield _sse_frame(event)

    # 响应与依赖全部准备好才启动pump，避免构造响应失败后后台仍偷偷执行。
    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    coroutine = pump()
    try:
        asyncio.create_task(coroutine)
    except BaseException:
        coroutine.close()
        raise
    return response


@router.post("/{session_id}/stop")
async def stop_turn(session_id: str) -> dict:
    """请求停止当前 turn：循环在下一个检查点优雅收口（部分输出保留）。"""
    if not turn_manager.request_stop(session_id):
        raise HTTPException(status_code=404, detail="该会话没有进行中的 turn")
    return {"ok": True}


class ApprovalIn(BaseModel):
    """三项审批；旧 approved 布尔字段仅兼容本次允许/拒绝，不授予会话权限。"""

    request_id: str
    option_id: str | None = None
    feedback: str | None = None
    approved: bool | None = None


@router.post("/{session_id}/approval")
async def submit_approval(session_id: str, body: ApprovalIn) -> dict:
    """原子校验归属/选项并唤醒；内存授权由等待方在停止检查后保存。"""
    info = approvals.get_request(body.request_id)
    if info is None or info["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
    # 显式传空字符串也是未知选项，不能通过布尔兼容字段意外放行。
    option_id = body.option_id
    if option_id is None and body.approved is not None:
        option_id = "allowOnce" if body.approved else "deny"
    if option_id not in {o["option_id"] for o in info["options"]}:
        raise HTTPException(status_code=400, detail="不支持的审批选项")
    if option_id == "allowSession" and info["scope"] is None:
        raise HTTPException(status_code=400, detail="无法确定会话授权范围")
    feedback = (body.feedback or "").strip()[:4096] or None
    if not approvals.resolve(body.request_id, option_id, session_id=session_id, feedback=feedback):
        raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
    return {"ok": True}
