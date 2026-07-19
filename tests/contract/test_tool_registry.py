"""[2026-07-19] 工具注册与执行统一拒绝未知、禁用、越权和无效 Schema 调用。"""

import asyncio

import pytest

from bai_agent.domain.models import ToolCall, ToolDefinition, ToolExecutionContext, ToolOutcome, ToolResult
from bai_agent.tools.executor import ToolExecutor
from bai_agent.tools.registry import ToolRegistry


class EchoTool:
    async def execute(self, arguments, context):
        return ToolResult(call_id="host", outcome=ToolOutcome.SUCCESS, data={"echo": arguments["text"], "flow_id": context.flow_id})


class SlowTool:
    async def execute(self, arguments, context):
        await asyncio.sleep(0.1)
        return ToolResult(call_id="host", outcome=ToolOutcome.SUCCESS)


class SerialProbeTool:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def execute(self, arguments, context):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return ToolResult(call_id="host", outcome=ToolOutcome.SUCCESS)


def definition(name="echo"):
    return ToolDefinition(
        tool_id=name,
        name=name,
        description="无副作用测试工具",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        safety={"read_only": True, "destructive": False},
    )


def context(persona="chat"):
    return ToolExecutionContext(
        flow_id="flow-host", turn_id="turn-host", persona_id=persona, state_id="default", config_revision="sha256:" + "1" * 64
    )


@pytest.mark.asyncio
async def test_unknown_disabled_and_persona_denied_have_stable_outcomes() -> None:
    registry = ToolRegistry()
    registry.register(definition(), EchoTool(), enabled=False, allowed_personas=("chat",))
    executor = ToolExecutor(registry)
    disabled = await executor.execute(ToolCall(call_id="c1", name="echo", arguments={"text": "x"}), context())
    unknown = await executor.execute(ToolCall(call_id="c2", name="imagined", arguments={}), context())
    assert disabled.outcome == ToolOutcome.DENIED and disabled.error_code == "TOOL_DISABLED"
    assert unknown.outcome == ToolOutcome.NOT_FOUND and unknown.error_code == "TOOL_NOT_FOUND"

    allowed_registry = ToolRegistry()
    allowed_registry.register(definition(), EchoTool(), enabled=True, allowed_personas=("chat",))
    denied = await ToolExecutor(allowed_registry).execute(
        ToolCall(call_id="c3", name="echo", arguments={"text": "x"}), context("memory_curator")
    )
    assert denied.outcome == ToolOutcome.DENIED


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{}, {"text": "x", "persona_id": "admin"}, {"text": 3}])
async def test_missing_extra_and_wrong_type_arguments_are_rejected(arguments: dict) -> None:
    registry = ToolRegistry()
    registry.register(definition(), EchoTool(), enabled=True, allowed_personas=("*",))
    result = await ToolExecutor(registry).execute(ToolCall(call_id="c", name="echo", arguments=arguments), context())
    assert result.outcome == ToolOutcome.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_timeout_and_oversized_result_are_bounded() -> None:
    slow = ToolRegistry()
    slow.register(definition("slow"), SlowTool(), enabled=True, allowed_personas=("*",))
    timed = await ToolExecutor(slow, deadline_seconds=0.001).execute(
        ToolCall(call_id="slow", name="slow", arguments={"text": "x"}), context()
    )
    assert timed.outcome == ToolOutcome.TIMEOUT

    registry = ToolRegistry()
    registry.register(definition(), EchoTool(), enabled=True, allowed_personas=("*",))
    oversized = await ToolExecutor(registry, max_result_bytes=8).execute(
        ToolCall(call_id="large", name="echo", arguments={"text": "too-large"}), context()
    )
    assert oversized.outcome == ToolOutcome.EXECUTION_FAILURE
    assert oversized.error_code == "TOOL_RESULT_TOO_LARGE"


@pytest.mark.asyncio
async def test_executor_serializes_concurrent_calls() -> None:
    probe = SerialProbeTool()
    registry = ToolRegistry()
    registry.register(definition(), probe, enabled=True, allowed_personas=("*",))
    executor = ToolExecutor(registry)
    await asyncio.gather(
        executor.execute(ToolCall(call_id="a", name="echo", arguments={"text": "a"}), context()),
        executor.execute(ToolCall(call_id="b", name="echo", arguments={"text": "b"}), context()),
    )
    assert probe.max_active == 1
