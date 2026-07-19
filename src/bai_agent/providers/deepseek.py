"""[2026-07-19] DeepSeek 适配器封装 OpenAI 兼容 SDK，并只返回领域 DTO。"""

from __future__ import annotations

import asyncio
from typing import Any

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import CompletionRequest, CompletionResult


class DeepSeekProvider:
    def __init__(self, client: Any, profile: dict[str, Any]) -> None:
        self.client = client
        self.profile = dict(profile)
        if self.profile.get("stream", False):
            raise BaiError("PROVIDER_CAPABILITY_INVALID", "首版必须使用完整非流式响应。")

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        attempts = max(1, int(self.profile.get("max_attempts", 1)))
        for attempt in range(attempts):
            try:
                response = await self.client.chat.completions.create(
                    model=self.profile["model"],
                    messages=[{"role": item.role, "content": item.content} for item in request.messages],
                    stream=False,
                    max_tokens=self.profile.get("max_output_tokens"),
                )
                if not response.choices:
                    raise BaiError("PROVIDER_PROTOCOL_INVALID", "模型响应不包含候选结果。")
                choice = response.choices[0]
                if choice.finish_reason == "length":
                    raise BaiError("PROVIDER_TRUNCATED", "模型响应因长度限制而不完整。", retryable=True)
                content = getattr(choice.message, "content", None)
                if not isinstance(content, str) or not content:
                    raise BaiError("PROVIDER_PROTOCOL_INVALID", "模型响应正文为空或无效。")
                usage = getattr(response, "usage", None)
                return CompletionResult(
                    text=content,
                    finish_reason=str(choice.finish_reason or "stop"),
                    usage={
                        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                    },
                )
            except asyncio.CancelledError:
                raise
            except BaiError:
                raise
            except Exception as exc:
                if attempt + 1 >= attempts:
                    raise BaiError("PROVIDER_FAILED", "模型调用失败。", retryable=True) from exc
                await asyncio.sleep(min(0.05 * (2**attempt), 0.2))
        raise BaiError("PROVIDER_FAILED", "模型调用失败。", retryable=True)

