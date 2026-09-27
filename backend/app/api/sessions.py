"""会话路由：新建（draft）、列表、详情（全量回放）、删除（软删）。

BE-3 范围只含 CRUD + resume 读取；turn/SSE/审批/重新生成在 BE-5/6/9 加入。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DbSession

from app import db
from app.models import new_id
from app.sessions import replay, store, turn_manager

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


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
