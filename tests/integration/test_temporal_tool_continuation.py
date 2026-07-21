"""[2026-07-20] Gateway/Executor 的工具事件时间只在成功边界采样一次。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bai_agent.application import build_application
from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CompletionResult, ToolCall, ToolDefinition, ToolExecutionContext, ToolOutcome, ToolResult,
)
from bai_agent.model_calls.gateway import ModelCallGateway
from bai_agent.tools.executor import ToolExecutor
from bai_agent.tools.registry import ToolRegistry
from tests.prompt_debug_fakes import FakeAdapter, UnavailableEstimator, make_draft


class SequenceClock:
    def __init__(self, *values) -> None:
        self.values = iter(values)
        self.calls = 0

    def now(self):
        self.calls += 1
        return next(self.values)


@pytest.mark.asyncio
async def test_gateway_samples_tool_acceptance_only_after_successful_retry() -> None:
    accepted = datetime(2026, 7, 20, tzinfo=timezone.utc)
    class ToolAdapter(FakeAdapter):
        async def send_once(self, payload):
            self.sent.append(payload)
            if self.failures:
                raise self.failures.pop(0)
            return CompletionResult(
                text="", finish_reason="tool_calls",
                tool_calls=({"call_id": "call-1", "name": "memory_source_query", "arguments": {}},),
            )

    adapter = ToolAdapter(failures=[BaiError("PROVIDER_FAILED", "retry", retryable=True)])
    clock = SequenceClock(accepted)
    gateway = ModelCallGateway(
        adapter, estimator=UnavailableEstimator(), max_attempts=2, backoff_seconds=0, clock=clock
    )
    result = await gateway.complete(make_draft())
    assert result.accepted_at == accepted
    assert result.origin_call_id == "call-1"
    assert clock.calls == 1


@pytest.mark.asyncio
async def test_executor_samples_completion_after_sendable_result_and_excludes_time() -> None:
    completed = datetime(2026, 7, 20, 1, tzinfo=timezone.utc)

    class Tool:
        async def execute(self, arguments, context):
            return ToolResult(call_id="host", outcome=ToolOutcome.SUCCESS, data={"value": "ok"})

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="test", name="test", description="test",
            input_schema={"type": "object"}, output_schema={"type": "object"},
        ),
        Tool(), enabled=True, allowed_personas=("chat",), read_only=True,
    )
    clock = SequenceClock(completed)
    result = await ToolExecutor(registry, clock=clock).execute(
        ToolCall(call_id="call-tool", name="test", arguments={}),
        ToolExecutionContext(
            flow_id="flow", turn_id="turn", persona_id="chat", state_id="default",
            config_revision="sha256:" + "1" * 64,
        ),
    )
    assert result.completed_at == completed
    assert result.origin_id.startswith("tool-result:call-tool:")
    assert "completed_at" not in result.model_dump(mode="json")
    assert "origin_id" not in result.model_dump(mode="json")
    assert clock.calls == 1


@pytest.mark.asyncio
async def test_four_continuations_rebuild_one_block_without_duplicate_markers(tmp_path: Path) -> None:
    class FourToolsThenAnswer(FakeAdapter):
        async def send_once(self, payload):
            self.sent.append(payload)
            round_index = len(self.sent)
            if round_index <= 4:
                return CompletionResult(
                    text="", finish_reason="tool_calls",
                    tool_calls=(
                        {
                            "call_id": f"call-{round_index}",
                            "name": "memory_source_query",
                            "arguments": {"memory_id": "mem-00000000-0000-4000-8000-000000000099"},
                        },
                    ),
                )
            return CompletionResult(text="完成", finish_reason="stop")

    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    clock = SequenceClock(*(start + timedelta(minutes=index) for index in range(10)))
    adapter = FourToolsThenAnswer()
    app = build_application(Path("config"), tmp_path / "data", provider=adapter, clock=clock)
    try:
        assert await app.run_turn("连续查四次") == "完成"
    finally:
        app.close()
    assert len(adapter.sent) == 5
    final_messages = adapter.sent[-1].sdk_kwargs["messages"]
    visible_tool_history = "".join(
        str(message["content"])
        for message in final_messages
        if message["role"] in {"assistant", "tool"}
    )
    tool_messages = [message for message in final_messages if message["role"] in {"assistant", "tool"}]
    assert all(
        message["content"].count("[UNTRUSTED tool_history.event.") == 1
        and message["content"].count("[/UNTRUSTED tool_history.event.") == 1
        for message in tool_messages
    )
    assert visible_tool_history.count("[时间：") == 1
    assert clock.calls == 10
