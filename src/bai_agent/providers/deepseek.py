"""[2026-07-19] DeepSeek 适配器封装 OpenAI 兼容 SDK，并只返回领域 DTO。"""

from __future__ import annotations

import asyncio
import json
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
                messages = []
                for item in request.messages:
                    mapped = {"role": item.role, "content": item.content}
                    if item.tool_call_id:
                        mapped["tool_call_id"] = item.tool_call_id
                    messages.append(mapped)
                tools = []
                for definition in request.tool_definitions:
                    if definition.get("type") == "function" and "function" in definition:
                        tools.append(definition)
                    else:
                        tools.append(
                            {
                                "type": "function",
                                "function": {
                                    "name": definition["name"],
                                    "description": definition.get("description", ""),
                                    "parameters": definition["input_schema"],
                                },
                            }
                        )
                response = await self.client.chat.completions.create(
                    model=self.profile["model"],
                    messages=messages,
                    stream=False,
                    max_tokens=self.profile.get("max_output_tokens"),
                    **({"tools": tools} if tools else {}),
                )
                if not response.choices:
                    raise BaiError("PROVIDER_PROTOCOL_INVALID", "模型响应不包含候选结果。")
                choice = response.choices[0]
                if choice.finish_reason == "length":
                    raise BaiError("PROVIDER_TRUNCATED", "模型响应因长度限制而不完整。", retryable=True)
                raw_tool_calls = getattr(choice.message, "tool_calls", None) or ()
                tool_calls = []
                seen_ids: set[str] = set()
                for raw_call in raw_tool_calls:
                    try:
                        call_id = str(raw_call.id)
                        name = str(raw_call.function.name)
                        arguments = json.loads(raw_call.function.arguments)
                        if not isinstance(arguments, dict) or not call_id or not name or call_id in seen_ids:
                            raise ValueError
                    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise BaiError("PROVIDER_TOOL_CALL_INVALID", "模型工具调用格式无效。") from exc
                    seen_ids.add(call_id)
                    tool_calls.append({"call_id": call_id, "name": name, "arguments": arguments})
                content = getattr(choice.message, "content", None)
                if (not isinstance(content, str) or not content) and not tool_calls:
                    raise BaiError("PROVIDER_PROTOCOL_INVALID", "模型响应正文为空或无效。")
                usage = getattr(response, "usage", None)
                return CompletionResult(
                    text=content or "",
                    finish_reason=str(choice.finish_reason or "stop"),
                    tool_calls=tuple(tool_calls),
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
