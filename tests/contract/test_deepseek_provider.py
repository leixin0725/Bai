"""[2026-07-19] DeepSeek 适配器归一化结果和错误，并过滤供应商推理字段。"""

from types import SimpleNamespace

import asyncio
import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import CompletionRequest, Message, ModelCallDraft
from bai_agent.model_calls.gateway import ModelCallGateway
from bai_agent.providers.deepseek import DeepSeekProvider
from tests.prompt_debug_fakes import make_draft


async def send(provider: DeepSeekProvider, request: CompletionRequest):
    draft = make_draft(request.messages[0].content if request.messages else "测试")
    draft = draft.model_copy(update={"request": request, "parts": draft.parts if request.messages else ()})
    prepared = provider.prepare(draft, 1)
    return await provider.send_once(provider.materialize_sdk_kwargs(prepared))


class FakeCompletions:
    def __init__(self, response=None, failure=None) -> None:
        self.response = response
        self.failure = failure
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.failure:
            raise self.failure
        return self.response


class EventuallySuccessfulCompletions(FakeCompletions):
    def __init__(self) -> None:
        super().__init__(response())
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("transient")
        return await super().create(**kwargs)


def response(content="完成", finish="stop"):
    message = SimpleNamespace(content=content, reasoning_content="不得外泄", tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3))


@pytest.mark.asyncio
async def test_complete_maps_request_and_filters_reasoning() -> None:
    completions = FakeCompletions(response())
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = DeepSeekProvider(client, {"model": "configured-model", "stream": False, "max_output_tokens": 32})
    result = await send(provider,
        CompletionRequest(flow_id="flow", turn_id="turn", messages=(Message(role="user", content="问题"),))
    )
    assert result.text == "完成"
    assert "reasoning" not in result.model_dump_json()
    assert completions.kwargs["stream"] is False


@pytest.mark.asyncio
async def test_truncation_and_sdk_failure_are_normalized() -> None:
    truncated = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(response("部分", "length"))))
    with pytest.raises(BaiError) as raised:
        await send(DeepSeekProvider(truncated, {"model": "m", "stream": False}),
            CompletionRequest(flow_id="f", turn_id="t")
        )
    assert raised.value.code == "PROVIDER_TRUNCATED"

    failed = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(failure=RuntimeError("sdk detail"))))
    with pytest.raises(BaiError) as raised:
        await send(DeepSeekProvider(failed, {"model": "m", "stream": False}),
            CompletionRequest(flow_id="f", turn_id="t")
        )
    assert raised.value.code == "PROVIDER_FAILED"
    assert "sdk detail" not in str(raised.value)


@pytest.mark.asyncio
async def test_cancellation_is_rethrown() -> None:
    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(failure=asyncio.CancelledError())))
    with pytest.raises(asyncio.CancelledError):
        await send(DeepSeekProvider(client, {"model": "m", "stream": False}),
            CompletionRequest(flow_id="f", turn_id="t")
        )


@pytest.mark.asyncio
async def test_retry_is_bounded_and_stream_profile_is_rejected() -> None:
    completions = EventuallySuccessfulCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    adapter = DeepSeekProvider(client, {"model": "m", "stream": False, "max_output_tokens": 32})
    result = await ModelCallGateway(adapter, max_attempts=3, backoff_seconds=0).complete(make_draft())
    assert result.text == "完成"
    assert completions.calls == 3
    with pytest.raises(BaiError) as raised:
        DeepSeekProvider(client, {"model": "m", "stream": True})
    assert raised.value.code == "PROVIDER_CAPABILITY_INVALID"
