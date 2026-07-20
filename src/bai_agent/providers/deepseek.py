"""[2026-07-19] DeepSeek 适配器封装 OpenAI 兼容 SDK，并只返回领域 DTO。"""

from __future__ import annotations

import json
from typing import Any

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CompletionResult,
    MaterializedSendPayload,
    ModelCallDraft,
    PreparedProviderRequest,
    Participation,
    RequestPart,
    SourceKind,
    SourceRef,
    TrustLevel,
    canonical_json,
)


class DeepSeekProvider:
    def __init__(self, client: Any, profile: dict[str, Any]) -> None:
        self.client = client
        self.profile = dict(profile)
        if self.profile.get("stream", False):
            raise BaiError("PROVIDER_CAPABILITY_INVALID", "首版必须使用完整非流式响应。")
        self.active_payload: MaterializedSendPayload | None = None

    def prepare(self, draft: ModelCallDraft, attempt: int) -> PreparedProviderRequest:
        """[2026-07-20] 准备阶段无 I/O，仅形成 provider-specific 无认证逻辑请求。"""
        messages = []
        for item in draft.request.messages:
            mapped = {"role": item.role, "content": item.content}
            if item.tool_call_id:
                mapped["tool_call_id"] = item.tool_call_id
            messages.append(mapped)
        tools = []
        for definition in draft.request.tool_definitions:
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
        kwargs: dict[str, Any] = {
            "model": self.profile["model"],
            "messages": messages,
            "stream": False,
            "max_tokens": self.profile.get("max_output_tokens"),
        }
        if tools:
            kwargs["tools"] = tools
        if "temperature" in self.profile:
            kwargs["temperature"] = self.profile["temperature"]
        if self.profile.get("structured_output") and self.profile.get("output_schema"):
            kwargs["response_format"] = {"type": "json_object"}
        parts = list(draft.parts)
        if tools:
            parts.append(
                RequestPart(
                    part_id=f"{draft.call_id}:tools",
                    order=len(parts),
                    participation=Participation.INCLUDED,
                    trust=TrustLevel.TRUSTED_INSTRUCTION,
                    payload_pointer="/tools",
                    content=canonical_json(tools),
                    sources=(
                        SourceRef(
                            source_kind=SourceKind.CONFIG_FILE,
                            source_id="tool-definitions",
                            project_relative_path="config/tools.toml",
                            content_sha256="sha256:" + "0" * 64,
                            revision=draft.config_revision,
                            entity_ids=tuple(str(item["function"]["name"]) for item in tools),
                            producer="tool_registry",
                        ),
                    ),
                )
            )
        return PreparedProviderRequest(
            call_id=draft.call_id,
            attempt=attempt,
            provider_id=str(self.profile.get("provider_id", "deepseek")),
            model=str(self.profile["model"]),
            provider_request=kwargs,
            max_output_tokens=int(self.profile.get("max_output_tokens", 8192)),
            parts=tuple(parts),
            call_sequence=draft.call_sequence,
            purpose=draft.purpose,
            turn_id=draft.turn_id,
            flow_id=draft.flow_id,
            persona_id=draft.persona_id,
            state_id=draft.state_id,
            config_revision=draft.config_revision,
        )

    def materialize_sdk_kwargs(self, request: PreparedProviderRequest) -> MaterializedSendPayload:
        return MaterializedSendPayload.create(
            call_id=request.call_id,
            attempt=request.attempt,
            provider_id=request.provider_id,
            model=request.model,
            sdk_kwargs=request.provider_request,
        )

    async def send_once(self, payload: MaterializedSendPayload) -> CompletionResult:
        """[2026-07-20] 每次调用只做一次 I/O，并在成功、失败或取消时释放 sender 引用。"""
        self.active_payload = payload
        try:
            response = await self.client.chat.completions.create(**payload.sdk_kwargs)
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
            if not isinstance(content, str) and not tool_calls:
                raise BaiError("PROVIDER_PROTOCOL_INVALID", "模型响应正文为空或无效。")
            usage = getattr(response, "usage", None)
            usage_values: dict[str, int] = {}
            usage_reason = None
            if usage is None:
                usage_reason = "provider 未返回实际用量。"
            else:
                try:
                    input_tokens = int(usage.prompt_tokens)
                    output_tokens = int(usage.completion_tokens)
                    total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens))
                    if min(input_tokens, output_tokens, total_tokens) < 0 or total_tokens != input_tokens + output_tokens:
                        raise ValueError
                    usage_values = {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "total_tokens": total_tokens,
                    }
                except (AttributeError, TypeError, ValueError):
                    usage_reason = "provider 返回的实际用量无效或不守恒。"
            return CompletionResult(
                text=content or "",
                finish_reason=str(choice.finish_reason or "stop"),
                tool_calls=tuple(tool_calls),
                usage=usage_values,
                usage_unavailable_reason=usage_reason,
            )
        except BaiError:
            raise
        except Exception as exc:
            raise BaiError("PROVIDER_FAILED", "模型调用失败。", retryable=True) from exc
        finally:
            self.active_payload = None
