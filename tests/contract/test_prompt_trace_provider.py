"""[2026-07-20] DeepSeek 适配器测试证明唯一物化参数等于 SDK 实际接收字段。"""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

from bai_agent.domain.models import thaw_json
from bai_agent.providers.deepseek import DeepSeekProvider
from tests.prompt_debug_fakes import make_draft


class CaptureCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="完成", tool_calls=[]), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=9, completion_tokens=2, total_tokens=11),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "多行\n正文", "简体中文🙂", "\x1b[31m标签", "长" * 4096])
async def test_prepare_materialize_and_send_are_field_identical(content: str) -> None:
    completions = CaptureCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    adapter = DeepSeekProvider(
        client,
        {
            "model": "deepseek-v4-flash",
            "stream": False,
            "thinking_enabled": False,
            "max_output_tokens": 8192,
        },
    )
    draft = make_draft(content)
    prepared = adapter.prepare(draft, 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    result = await adapter.send_once(payload)
    assert completions.kwargs == thaw_json(payload.sdk_kwargs)
    assert completions.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert result.usage == {"input_tokens": 9, "output_tokens": 2, "total_tokens": 11}
    assert adapter.active_payload is None


@pytest.mark.asyncio
async def test_actual_openai_sdk_serializes_frozen_payload_without_value_drift() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads((await request.aread()).decode("utf-8"))
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "local-mock",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "完成"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="invalid-example-only",
        base_url="https://api.deepseek.com",
        max_retries=0,
        http_client=http_client,
    )
    adapter = DeepSeekProvider(
        client,
        {
            "model": "deepseek-v4-flash",
            "stream": False,
            "thinking_enabled": False,
            "max_output_tokens": 8192,
        },
    )
    payload = adapter.materialize_sdk_kwargs(adapter.prepare(make_draft("本地序列化回归"), 1))
    sdk_kwargs = thaw_json(payload.sdk_kwargs)
    expected_wire = {key: value for key, value in sdk_kwargs.items() if key != "extra_body"}
    expected_wire.update(sdk_kwargs["extra_body"])

    try:
        result = await adapter.send_once(payload)
    finally:
        await http_client.aclose()

    assert result.text == "完成"
    assert captured["json"] == expected_wire
    assert adapter.active_payload is None
