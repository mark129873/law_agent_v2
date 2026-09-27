"""日志与观测初始化模块。

设计（docs/RELIABILITY.md）：
- logging 双通道：控制台（人读）+ 滚动文件 data/logs/app.log；
- 只记工程事件（turn 生命周期/工具/审批/压缩/错误），不记对话正文；
- Langfuse 在 BE-10 接入，本模块预留 langfuse_enabled 判断位置。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

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
