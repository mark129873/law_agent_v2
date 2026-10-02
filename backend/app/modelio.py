"""model-io JSONL：逐次 LLM 调用的本地快照（ZCode 同思想，替代 Langfuse）。

定位（docs/RELIABILITY.md §2）：
- SQLite 会话存储管"对话事实"，这里管"每次调用的完整输入输出"——
  审计/调试时能回答"模型当时到底收到了什么、回了什么、花了多少"；
- 一个会话一个文件 `<MODELIO_DIR>/model-io-<session_id>.jsonl`，逐行 JSON 追加；
- 与 ZCode 的 model-io 同定位：本地文件、零外部依赖、含每次调用的
  system prompt / messages / response / 用量 / 耗时 / 错误。

纪律：写入失败吞异常记 WARNING——观测永远不能挡住对话主流程。
"""

import json
import logging
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def record_llm_call(
    session_id: str,
    turn_id: str,
    model: str,
    system: str,
    messages: list,
    response_text: str,
    tool_calls: list[dict],
    usage: dict,
    duration_ms: float,
    tool_names: list[str] | None = None,
    subtask_id: str | None = None,
    error: str | None = None,
) -> None:
    """把一次 LLM 调用追加写入 model-io JSONL（一行一条 JSON）。

    参数即调用现场：system/messages 为调用时快照，response_text/tool_calls
    为该步产出，usage 为该次 API 返回的真实用量，error 非空表示该次调用失败。
    任何写入失败都吞掉并记 WARNING。
    """
    try:
        record = {
            "time": time.time() * 1000.0,  # epoch ms，与全库时间戳口径一致
            "session_id": session_id,
            "turn_id": turn_id,
            "subtask_id": subtask_id,  # 仅子助手调用非空
            "model": model,
            "duration_ms": round(duration_ms, 1),
            "error": error,  # None=成功；字符串=该次调用的失败原因
            "request": {
                "system": system,  # 本次调用的系统提示词全文
                "messages": messages,  # 本次调用的完整消息历史快照
                "tool_names": tool_names or [],  # 提供给模型的工具名（不含 schema）
            },
            "response": {
                "text": response_text,  # 该步产出的正文
                "tool_calls": tool_calls,  # 该步发起的工具调用 [{id,name,input}]
            },
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                # 缓存计量：端点支持 prompt caching 才有非零值（Anthropic 协议字段）
                "cache_read_tokens": usage.get("cache_read_tokens", 0),
                "cache_creation_tokens": usage.get("cache_creation_tokens", 0),
            },
        }
        target = Path(settings.modelio_dir)
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"model-io-{session_id}.jsonl"
        # 大小轮转：超限的老文件改名归档（带时间戳），新调用写进新文件——
        # 防超长会话单文件无限增长（docs/RELIABILITY.md §2）
        max_bytes = max(1, int(settings.modelio_max_bytes))
        if path.exists() and path.stat().st_size > max_bytes:
            # 纳秒后缀避免同毫秒轮转改名碰撞（Windows 上 rename 不覆盖同名文件）
            path.rename(target / f"model-io-{session_id}-{time.time_ns()}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # 观测失败不影响主流程
        logger.warning("model-io 写入失败（忽略）：%s", exc)
