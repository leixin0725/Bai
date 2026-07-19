"""[2026-07-19] DeepSeek 工具协议映射拒绝无效 JSON 和重复 call ID，不泄漏 SDK 类型。"""

from types import SimpleNamespace

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import CompletionRequest, Message
from bai_agent.providers.deepseek import DeepSeekProvider


class Completions:
    def __init__(self, response) -> None:
        self.response = response
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def tool_call(call_id: str, arguments: str, name: str = "memory_source_query"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments), type="function")


def response(calls):
    message = SimpleNamespace(content=None, reasoning_content="内部推理", tool_calls=calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


@pytest.mark.asyncio
async def test_multiple_tool_calls_and_definition_mapping_are_domain_dtos() -> None:
    completions = Completions(response([tool_call("c1", '{"memory_id":"mem-a"}'), tool_call("c2", '{"memory_id":"mem-b"}')]))
    provider = DeepSeekProvider(SimpleNamespace(chat=SimpleNamespace(completions=completions)), {"model": "m", "stream": False})
    result = await provider.complete(
        CompletionRequest(
            flow_id="f", turn_id="t",
            tool_definitions=({"type": "function", "function": {"name": "memory_source_query", "parameters": {"type": "object"}}},),
        )
    )
    assert [call["call_id"] for call in result.tool_calls] == ["c1", "c2"]
    assert completions.kwargs["tools"][0]["function"]["name"] == "memory_source_query"
    assert "SimpleNamespace" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "calls",
    [
        [tool_call("same", "{}"), tool_call("same", "{}")],
        [tool_call("bad", "not-json")],
        [tool_call("array", "[]")],
    ],
)
async def test_duplicate_call_id_and_invalid_arguments_fail_before_execution(calls) -> None:
    provider = DeepSeekProvider(
        SimpleNamespace(chat=SimpleNamespace(completions=Completions(response(calls)))),
        {"model": "m", "stream": False},
    )
    with pytest.raises(BaiError) as raised:
        await provider.complete(CompletionRequest(flow_id="f", turn_id="t"))
    assert raised.value.code == "PROVIDER_TOOL_CALL_INVALID"


@pytest.mark.asyncio
async def test_tool_call_id_is_returned_to_provider() -> None:
    completions = Completions(
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="完成", reasoning_content=None, tool_calls=None), finish_reason="stop")],
            usage=None,
        )
    )
    provider = DeepSeekProvider(SimpleNamespace(chat=SimpleNamespace(completions=completions)), {"model": "m", "stream": False})
    await provider.complete(
        CompletionRequest(
            flow_id="f", turn_id="t",
            messages=(Message(role="tool", content='{"ok":true}', tool_call_id="call-1"),),
        )
    )
    assert completions.kwargs["messages"][0]["tool_call_id"] == "call-1"

