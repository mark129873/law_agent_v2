"""LLM 流式客户端：Anthropic 兼容 API 的流式封装。

客户端协议（loop.py 消费的 duck type）：
    async def stream(*, system: str, messages: list, tools: list) -> AsyncIterator[dict]
事件类型：
    {"type": "text_delta", "text": "..."}
    {"type": "tool_use", "id": "...", "name": "...", "input": {...}}   # 块结束时一次性吐出
    {"type": "usage", "input_tokens": n, "output_tokens": n}

为什么用 messages.stream 上下文：SDK 会自动聚合同步帧；tool_use 的参数
以 input_json_delta 分片到达，这里按块索引攒齐后 json 解析再吐出。
"""

import json
import logging
from typing import AsyncIterator

from anthropic import AsyncAnthropic

from app.config import Settings

logger = logging.getLogger(__name__)


class AnthropicStreamClient:
    """Anthropic 兼容流式客户端（thinking 全局关闭，与 mini_harness 一致）。"""

    def __init__(self, s: Settings) -> None:
        # base_url 为空时用官方默认；兼容端点（MiniMax/GLM/Kimi 等）填各自地址
        kwargs: dict = {"api_key": s.anthropic_api_key}
        if s.anthropic_base_url:
            kwargs["base_url"] = s.anthropic_base_url
        self._client = AsyncAnthropic(**kwargs)
        self.model = s.model_id  # 公开属性：观测上报用
        self._max_tokens = s.max_tokens

    async def stream(
        self, *, system: str, messages: list, tools: list
    ) -> AsyncIterator[dict]:
        async with self._client.messages.stream(
            model=self.model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=self._max_tokens,
            thinking={"type": "disabled"},
        ) as stream:
            tool_meta: dict[int, dict] = {}  # 块索引 -> {id,name}
            json_fragments: dict[int, str] = {}  # 块索引 -> 攒参数 JSON 分片

            async for event in stream:
                etype = event.type
                if etype == "content_block_start":
                    block = event.content_block
                    if getattr(block, "type", "") == "tool_use":
                        tool_meta[event.index] = {"id": block.id, "name": block.name}
                elif etype == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield {"type": "text_delta", "text": delta.text}
                    elif delta.type == "input_json_delta":
                        json_fragments[event.index] = (
                            json_fragments.get(event.index, "") + delta.partial_json
                        )
                elif etype == "content_block_stop":
                    meta = tool_meta.pop(event.index, None)
                    if meta is not None:
                        raw = json_fragments.pop(event.index, "")
                        try:
                            args = json.loads(raw) if raw else {}
                        except ValueError:
                            logger.warning("tool_use 参数 JSON 解析失败，按空参数处理")
                            args = {}
                        yield {
                            "type": "tool_use",
                            "id": meta["id"],
                            "name": meta["name"],
                            "input": args,
                        }

            # 流结束：真实 token 用量（usage 事件驱动 token_count 与工时累计）
            final = stream.get_final_message()
            usage = final.usage
            yield {
                "type": "usage",
                "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            }


def build_client(s: Settings) -> AnthropicStreamClient:
    """构造客户端（API 路由经依赖注入使用，测试可替换）。"""
    return AnthropicStreamClient(s)
