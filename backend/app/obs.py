"""日志与观测初始化模块。

设计（docs/RELIABILITY.md）：
- logging 双通道：控制台（人读）+ 滚动文件 data/logs/app.log；
- 只记工程事件（turn 生命周期/工具/审批/压缩/错误），不记对话正文；
- Langfuse 在 BE-10 接入，本模块预留 langfuse_enabled 判断位置。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)

# 日志格式：时间 | 级别 | 模块 | 内容（人读友好）
_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(data_dir: Path, level: str = "INFO") -> None:
    """初始化根日志：控制台 + 滚动文件。

    为什么每次先关闭并清空 handlers：
    1. uvicorn --reload 或测试里可能多次调用，重复挂 handler 会导致同一条日志打印多遍；
    2. Windows 上文件句柄未关闭时目录无法删除（测试清理需要），所以旧 handler 要显式 close。
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        try:
            handler.close()
        except Exception:  # 关闭失败不影响重新初始化
            pass
        root.removeHandler(handler)
    root.handlers.clear()

    formatter = logging.Formatter(_LOG_FORMAT)

    # 控制台通道
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    # 滚动文件通道：单文件 5MB，保留 5 份（app.log.1 ~ app.log.5）
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 降低第三方库噪音
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def shutdown_logging() -> None:
    """关闭并移除根日志的全部 handler。

    为什么需要：Windows 上文件 handler 未 close 时 app.log 被占用，
    测试清理（删除 .tmp-data/）会失败；进程退出前/测试收尾时调用。
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        try:
            handler.flush()
            handler.close()
        except Exception:  # 清理失败不抛出，避免掩盖测试本身的错误
            pass
        root.removeHandler(handler)


# ---------- Langfuse（BE-10：与 logging 分工——这里记 LLM 交互细节，logging 记工程事件） ----------

_langfuse_client = None  # 进程内共享；未启用时为 None


def init_langfuse(settings) -> None:
    """按配置初始化 Langfuse；默认关闭，开启后失败也不影响主流程。"""
    global _langfuse_client
    if not getattr(settings, "langfuse_enabled", False):
        logger.info("Langfuse 未启用（LANGFUSE_ENABLED=false）")
        return
    try:
        from langfuse import Langfuse

        kwargs = {
            "public_key": settings.langfuse_public_key,
            "secret_key": settings.langfuse_secret_key,
        }
        if settings.langfuse_base_url:
            kwargs["host"] = settings.langfuse_base_url
        _langfuse_client = Langfuse(**kwargs)
        logger.info("Langfuse 已启用 host=%s", settings.langfuse_base_url or "(默认云版)")
    except Exception as exc:  # 初始化失败：降级为不观测，绝不阻断启动
        logger.warning("Langfuse 初始化失败（不影响主流程）：%s", exc)
        _langfuse_client = None


def record_llm_call(
    session_id: str,
    turn_id: str,
    model: str,
    system: str,
    messages: list,
    output_text: str,
    usage: dict,
    subtask_id: str | None = None,
) -> None:
    """上报一次 LLM 调用（trace=session，generation 带 turn 元数据）。

    双系统不重复的边界：这里记 prompt/messages/completion 全文，
    logging 里只记"发生了一次调用"的工程事实。
    subtask_id：subtask 子循环的调用传入，用于在观测里与主循环调用区分。
    任何失败都吞掉并记 WARNING——观测永远不能挡住对话主流程。
    """
    if _langfuse_client is None:
        return
    try:
        trace = _langfuse_client.trace(id=session_id, name="session", session_id=session_id)
        metadata: dict = {"turn_id": turn_id}
        if subtask_id:
            metadata["subtask_id"] = subtask_id
        trace.generation(
            name="llm-call",
            model=model,
            metadata=metadata,
            input={"system": system, "messages": messages},
            output=output_text,
            usage={
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
                "unit": "TOKENS",
            },
        )
        _langfuse_client.flush()
    except Exception as exc:
        logger.warning("Langfuse 上报失败（忽略）：%s", exc)
