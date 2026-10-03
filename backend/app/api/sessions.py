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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from app import db
from app.agent.llm import build_client
from app.agent.loop import TurnDeps, run_turn
from app.config import settings, validate_llm_config
from app.models import new_id
from app.sessions import replay, store, turn_manager
from app.sessions.approvals import InteractiveApprover
from app.sessions.recorder import TurnRecorder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# SSE 心跳间隔（秒）：注释帧保活，防止代理/浏览器掐断空闲连接
_KEEPALIVE_SECONDS = 15

_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "agent" / "system_prompt.md"


class TurnIn(BaseModel):
    """POST /turn 请求体。"""

    text: str


def get_llm_client() -> object:
    """LLM 客户端依赖（测试用 dependency_overrides 注入假客户端）。"""
    return build_client(settings)


def _load_system_prompt() -> str:
    """每次 turn 现读系统提示词文件：改动对其后的轮次立即生效（产品决策）。"""
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
                "id": row.id,
                "title": row.title,
                "model": row.model,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "tokens_used": row.tokens_used,
                "input_tokens": row.input_tokens,
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
        db_session, session_id, running_turn_ids=turn_manager.running_session_ids()
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

    # turn 使用独立 DB 会话：HTTP 请求结束时请求级会话会关闭，
    # 而后台泵任务要一直用到 turn 收口。
    turn_db = db.new_session()
    recorder = TurnRecorder(turn_db, session_id, model=settings.model_id, max_tokens=settings.max_tokens)
    stop_event = turn_manager.register(session_id, recorder.turn_id)

    history = replay.load_history(turn_db, session_id)
    history.append({"role": "user", "content": body.text})

    queue: asyncio.Queue = asyncio.Queue()
    deps = TurnDeps(
        client=client,
        recorder=recorder,
        system_prompt=_load_system_prompt(),
        history=history,
        stop_flag=stop_event.is_set,
        approver=InteractiveApprover(turn_db, session_id, recorder, queue),
        settings=settings,
        queue=queue,
    )
    return _turn_sse_response(session_id, recorder, stop_event, deps, turn_db)


@router.post("/{session_id}/regenerate")
async def regenerate_turn(
    session_id: str,
    client: object = Depends(get_llm_client),
) -> StreamingResponse:
    """重新生成：保留最后一轮的用户消息，回滚其后内容并重跑（错误重试同机制）。"""
    _check_llm_config()
    _check_not_running(session_id)

    turn_db = db.new_session()
    last_user = store.find_last_user_message(turn_db, session_id)
    if last_user is None:
        turn_db.close()
        raise HTTPException(status_code=404, detail="没有可重新生成的轮次")

    recorder = TurnRecorder(turn_db, session_id, model=settings.model_id, max_tokens=settings.max_tokens)
    stop_event = turn_manager.register(session_id, recorder.turn_id)

    # 回滚：删该轮 assistant 行（级联 parts）与该轮事实；用户消息保留并改挂新轮
    store.rollback_turn(turn_db, session_id, last_user.turn_id, last_user.sequence)
    # 重算会话用量：被删轮的累计不再计入（旧实现漏了这步，被删轮 token 会双算）
    replay.recalc_session_usage(turn_db, session_id)
    last_user.turn_id = recorder.turn_id
    turn_db.commit()

    history = replay.load_history(turn_db, session_id)  # 以保留的用户消息结尾

    queue: asyncio.Queue = asyncio.Queue()
    deps = TurnDeps(
        client=client,
        recorder=recorder,
        system_prompt=_load_system_prompt(),
        history=history,
        stop_flag=stop_event.is_set,
        approver=InteractiveApprover(turn_db, session_id, recorder, queue),
        settings=settings,
        queue=queue,
        persist_user_message=False,  # 用户消息已存在，不重复落盘
        user_sequence=last_user.sequence,
    )
    return _turn_sse_response(session_id, recorder, stop_event, deps, turn_db)


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
    session_id: str, recorder, stop_event, deps: TurnDeps, turn_db
) -> StreamingResponse:
    """turn 的公共 SSE 出口：后台泵任务 + 队列 + 心跳（start_turn/regenerate 共用）。"""
    # 复用路由层创建的那个队列：InteractiveApprover 持有同一引用直推审批事件，
    # 若在这里另建新队列，审批事件会被写进无人消费的旧队列而丢失（实测踩过的坑）
    queue = deps.queue
    assert queue is not None, "TurnDeps.queue 必须由路由层预先创建"

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

    asyncio.create_task(pump())

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

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{session_id}/stop")
def stop_turn(session_id: str) -> dict:
    """请求停止当前 turn：循环在下一个检查点优雅收口（部分输出保留）。"""
    if not turn_manager.request_stop(session_id):
        raise HTTPException(status_code=404, detail="该会话没有进行中的 turn")
    return {"ok": True}


class ApprovalIn(BaseModel):
    """POST /approval 请求体。

    option_id：allowOnce / allowAlways / deny / fullAccess（弹窗动态选项的应答）；
    approved：旧客户端兼容字段（True→allowOnce，False→deny）；
    feedback：拒绝时的用户反馈（≤4096 字符，拼进喂回模型的理由）。
    """

    request_id: str
    option_id: str | None = None
    feedback: str | None = None
    approved: bool | None = None


@router.post("/{session_id}/approval")
def submit_approval(session_id: str, body: ApprovalIn) -> dict:
    """提交审批决定：唤醒正在等待的 turn（与连接无关，刷新后仍可提交）。

    选项语义（ZCode 同款）：
    - allowOnce：仅本次放行；
    - allowAlways：从注册表取 tool/input 推导规则并落盘，再放行本次；
    - fullAccess：会话协作模式切 yolo 持久化（一次性应答，不写规则），再放行本次；
    - deny / 未知 optionId：拒绝（未知 id 兜底拒绝，ZCode 同款）。
    """
    from app.agent.permission_service import derive_rule
    from app.sessions import approvals, execution_state

    option_id = body.option_id or ("allowOnce" if body.approved else "deny")
    feedback = (body.feedback or "").strip()[:4096] or None

    if option_id == "fullAccess":
        info = approvals.get_request(body.request_id)
        if info is None or not info.get("full_access"):
            raise HTTPException(status_code=404, detail="审批请求不存在或不支持完全访问")
        current = execution_state.load_execution_state(settings.data_dir)
        execution_state.save_execution_state(settings.data_dir, "yolo", current["plan_enabled"])
        if not approvals.resolve(body.request_id, True):
            raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
        logger.info("完全访问已授权 session=%s", session_id)
        return {"ok": True, "mode": "yolo"}

    if option_id == "allowAlways":
        info = approvals.get_request(body.request_id)
        if info is None:
            raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
        rule = derive_rule(info.get("tool") or "", info.get("input") or {})
        if rule:
            execution_state.add_permission_rule(
                settings.data_dir, "allow", rule["tool"], rule["content"]
            )
            logger.info("审批规则已保存 tool=%s content=%s", rule["tool"], rule["content"])

    if option_id == "allowOnce" or option_id == "allowAlways":
        approved, used_feedback = True, None
    elif option_id == "deny":
        approved, used_feedback = False, feedback
    else:
        # 未知 optionId 一律拒绝兜底（ZCode 同款）
        approved, used_feedback = False, feedback

    if not approvals.resolve(body.request_id, approved, used_feedback):
        raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
    return {"ok": True}
