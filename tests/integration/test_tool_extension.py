"""[2026-07-19] 附加工具显式启用才可调用，失败不改变聊天或记忆权威文件。"""

from hashlib import sha256
from pathlib import Path

import pytest

from bai_agent.domain.models import ToolCall, ToolDefinition, ToolExecutionContext, ToolOutcome, ToolResult
from bai_agent.tools.executor import ToolExecutor
from bai_agent.tools.registry import ToolRegistry


class HarmlessTool:
    async def execute(self, arguments, context):
        return ToolResult(call_id="host", outcome=ToolOutcome.SUCCESS, data={"value": arguments["value"]})


def harmless_definition():
    return ToolDefinition(
        tool_id="harmless", name="harmless", description="无副作用工具",
        input_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
        output_schema={"type": "object"}, safety={"read_only": True, "destructive": False},
    )


def execution_context():
    return ToolExecutionContext(
        flow_id="flow-1", turn_id="turn-1", persona_id="chat", state_id="default",
        config_revision="sha256:" + "1" * 64, trigger_record_id="rec-trigger",
    )


@pytest.mark.asyncio
async def test_default_registry_excludes_future_tool_until_explicit_enable() -> None:
    registry = ToolRegistry()
    registry.register(harmless_definition(), HarmlessTool(), enabled=False, allowed_personas=("chat",))
    assert registry.definitions_for("chat") == ()
    denied = await ToolExecutor(registry).execute(
        ToolCall(call_id="c", name="harmless", arguments={"value": "x"}), execution_context()
    )
    assert denied.outcome == ToolOutcome.DENIED

    enabled = ToolRegistry()
    enabled.register(harmless_definition(), HarmlessTool(), enabled=True, allowed_personas=("chat",))
    result = await ToolExecutor(enabled).execute(
        ToolCall(call_id="c", name="harmless", arguments={"value": "x"}), execution_context()
    )
    assert result.outcome == ToolOutcome.SUCCESS
