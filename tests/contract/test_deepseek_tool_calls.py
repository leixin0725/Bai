"""[2026-07-19] DeepSeek 工具协议映射拒绝无效 JSON 和重复 call ID，不泄漏 SDK 类型。"""

from types import SimpleNamespace

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CompletionRequest, Message, Participation, RequestPart, SourceKind, SourceRef, thaw_json,
)
from bai_agent.providers.deepseek import DeepSeekProvider
from tests.prompt_debug_fakes import make_draft


async def send(provider: DeepSeekProvider, request: CompletionRequest):
    draft = make_draft(request.messages[0].content if request.messages else "测试")
    draft = draft.model_copy(update={"request": request, "parts": draft.parts if request.messages else ()})
    prepared = provider.prepare(draft, 1)
    return await provider.send_once(provider.materialize_sdk_kwargs(prepared))


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
    result = await send(provider,
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
        await send(provider, CompletionRequest(flow_id="f", turn_id="t"))
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
    await send(provider,
        CompletionRequest(
            flow_id="f", turn_id="t",
            messages=(Message(role="tool", content='{"ok":true}', tool_call_id="call-1"),),
        )
    )
    assert completions.kwargs["messages"][0]["tool_call_id"] == "call-1"


@pytest.mark.asyncio
async def test_assistant_tool_call_is_replayed_before_tool_result() -> None:
    completions = Completions(
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="完成", tool_calls=None), finish_reason="stop")],
            usage=None,
        )
    )
    provider = DeepSeekProvider(
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        {"model": "deepseek-v4-flash", "stream": False, "thinking_enabled": False},
    )
    await send(
        provider,
        CompletionRequest(
            flow_id="f",
            turn_id="t",
            messages=(
                Message(role="user", content="查询来源"),
                Message(
                    role="assistant",
                    content="",
                    tool_calls=(
                        {
                            "call_id": "call-1",
                            "name": "memory_source_query",
                            "arguments": {"memory_id": "missing"},
                        },
                    ),
                ),
                Message(role="tool", content='{"outcome":"not_found"}', tool_call_id="call-1"),
            ),
        ),
    )

    assistant = thaw_json(completions.kwargs["messages"])[1]
    assert assistant == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "memory_source_query",
                    "arguments": '{"memory_id":"missing"}',
                },
            }
        ],
    }
    assert completions.kwargs["messages"][2]["tool_call_id"] == "call-1"


def test_upstream_tool_content_fragments_prevent_fallback_and_keep_origin_source() -> None:
    provider = DeepSeekProvider(
        SimpleNamespace(chat=SimpleNamespace(completions=None)),
        {"model": "m", "stream": False},
    )
    content = "[时间：2026-07-20 08:00 +08:00]\n"
    origin = SourceRef(
        source_kind=SourceKind.RUNTIME,
        source_id="model-response:origin-call",
        entity_ids=("origin-call", "call-1"),
        producer="model_call_gateway",
    )
    draft = make_draft(content).model_copy(
        update={
            "request": CompletionRequest(
                flow_id="f", turn_id="t",
                messages=(Message(
                    role="assistant", content=content,
                    tool_calls=({"call_id": "call-1", "name": "memory_source_query", "arguments": {}},),
                ),),
            ),
            "parts": (
                RequestPart(
                    part_id="marker", order=0, participation=Participation.INCLUDED,
                    trust="untrusted_data", payload_pointer="/messages/0/content",
                    text_span=(0, len(content) - 1), content=content[:-1], sources=(origin,),
                ),
                RequestPart(
                    part_id="separator", order=1, participation=Participation.INCLUDED,
                    trust="untrusted_data", payload_pointer="/messages/0/content",
                    text_span=(len(content) - 1, len(content)), content="\n", sources=(origin,),
                ),
            ),
        }
    )
    prepared = provider.prepare(draft, 1)
    content_parts = tuple(part for part in prepared.parts if part.payload_pointer == "/messages/0/content")
    tool_call_part = next(part for part in prepared.parts if part.payload_pointer == "/messages/0/tool_calls")
    assert [part.part_id for part in content_parts] == ["marker", "separator"]
    assert {source.source_id for source in tool_call_part.sources} == {"model-response:origin-call"}
