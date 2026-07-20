"""[2026-07-20] DeepSeek 适配器测试证明唯一物化参数等于 SDK 实际接收字段。"""

from types import SimpleNamespace

import pytest

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
    adapter = DeepSeekProvider(client, {"model": "deepseek-chat", "stream": False, "max_output_tokens": 8192})
    draft = make_draft(content)
    prepared = adapter.prepare(draft, 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    result = await adapter.send_once(payload)
    assert completions.kwargs == dict(payload.sdk_kwargs)
    assert result.usage == {"input_tokens": 9, "output_tokens": 2, "total_tokens": 11}
    assert adapter.active_payload is None
