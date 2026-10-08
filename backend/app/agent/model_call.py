"""主、子循环共用模型响应聚合与调用快照；循环自行决定事件、停止和落盘。"""

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from app.modelio import record_llm_call

_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


@dataclass
class ModelReply:
    """保存一个模型步的产出，让两个循环使用同一套聚合与用量字段。"""

    text: str = ""
    tool_uses: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    def accept(self, event: dict) -> None:
        """聚合模型事件；不处理SSE或存储，保留主、子循环各自的行为。"""
        kind = event.get("type")
        if kind == "text_delta":
            self.text += event.get("text", "")
        elif kind == "tool_use":
            self.tool_uses.append(event)
        elif kind == "usage":
            self.usage = {key: event.get(key, 0) for key in _USAGE_KEYS}

    def assistant_blocks(self) -> list[dict]:
        """按原顺序构造text与tool_use块，供后续工具结果回填。"""
        blocks = [{"type": "text", "text": self.text}] if self.text else []
        for tool in self.tool_uses:
            blocks.append({
                "type": "tool_use", "id": tool["id"], "name": tool["name"],
                "input": tool.get("input") or {},
            })
        return blocks


@contextmanager
def record_model_step(deps, system: str, messages: list, tools: list,
                      *, subtask_id: str | None = None) -> Iterator[ModelReply]:
    """统一记录调用现场；正常、异常和流中停止都通过finally写model-io。"""
    reply = ModelReply()
    snapshot = [dict(message) for message in messages]
    started = time.monotonic()
    error = None
    try:
        yield reply
    except Exception as exc:
        error = str(exc)
        raise  # 异常仍由各自循环收口，观测不改变错误处理。
    finally:
        record_llm_call(
            deps.recorder.session_id, deps.recorder.turn_id,
            getattr(deps.client, "model", "unknown"), system, snapshot,
            response_text=reply.text,
            tool_calls=[
                {"id": t["id"], "name": t["name"], "input": t.get("input") or {}}
                for t in reply.tool_uses
            ],
            usage=reply.usage,
            duration_ms=(time.monotonic() - started) * 1000,
            tool_names=[t["name"] for t in tools],
            subtask_id=subtask_id,
            error=error,
        )
