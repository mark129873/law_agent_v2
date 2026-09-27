"""FastAPI 应用入口。

职责（BE-1 范围）：
1. lifespan 启动时全自动初始化：建数据目录、初始化日志、建库建表（无手动步骤）；
2. 挂载 /api/health 健康检查；
3. 其余路由在后续功能中挂载（api/sessions.py 等）。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.api import sessions as sessions_api
from app.config import settings
from app.obs import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化运行环境，关闭时不需清理（WAL 自动落盘）。"""
    # 全自动初始化（产品决策：首次启动无手动步骤）
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "workspace").mkdir(parents=True, exist_ok=True)
    setup_logging(settings.data_dir, settings.log_level)
    db.init_db(settings.data_dir)
    # 默认 hooks：工具调用的工程事件日志（PreToolUse/PostToolUse）
    from app.agent import hooks

    hooks.register_default_hooks()

    import logging

    logging.getLogger(__name__).info(
        "后端启动完成：data_dir=%s model=%s", settings.data_dir, settings.model_id or "(未配置)"
    )
    yield


def create_app() -> FastAPI:
    """构造 FastAPI 实例（独立成函数方便测试复用）。"""
    app = FastAPI(title="个人助手 harness", lifespan=lifespan)
    app.include_router(sessions_api.router)

    @app.get("/api/health")
    def health() -> dict:
        """健康检查：不依赖 LLM 配置，进程活着即返回 ok。"""
        return {"status": "ok"}

    return app


app = create_app()
