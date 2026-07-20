"""[2026-07-20] 工具能力测试确保写副作用在可恢复能力验证前为零。"""

import asyncio

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolOutcome,
    ToolResult,
)
from bai_agent.tools.executor import ToolExecutor
from bai_agent.tools.registry import ToolRegistry


def definition(name: str) -> ToolDefinition:
    return ToolDefinition(tool_id=name, name=name, description="测试", input_schema={}, output_schema={})


def test_read_only_tool_is_allowed_and_unrecoverable_write_tool_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(definition("read"), object(), enabled=True, allowed_personas=("*",), read_only=True)
    assert registry.resolve("read", "chat").read_only
    with pytest.raises(BaiError, match="恢复"):
        registry.register(definition("write"), object(), enabled=True, allowed_personas=("*",), read_only=False)


def test_recoverable_write_tool_requires_complete_protocol() -> None:
    class Recoverable:
        def prepare(self): pass
        def commit(self): pass
        def rollback(self): pass

    registry = ToolRegistry()
    registry.register(definition("write"), Recoverable(), enabled=True, allowed_personas=("*",), read_only=False)
    assert not registry.resolve("write", "chat").read_only


@pytest.mark.asyncio
async def test_write_tool_rolls_back_execution_failure_before_commit() -> None:
    events: list[str] = []

    class Recoverable:
        def prepare(self, arguments, context):
            events.append("prepare")
            return "token"

        async def execute(self, arguments, context):
            events.append("execute")
            raise RuntimeError("失败正文")

        def commit(self, token):
            events.append("commit")

        def rollback(self, token):
            events.append(f"rollback:{token}")

    registry = ToolRegistry()
    registry.register(
        definition("write"), Recoverable(), enabled=True,
        allowed_personas=("chat",), read_only=False,
    )
    result = await ToolExecutor(registry).execute(
        ToolCall(call_id="call-write", name="write", arguments={}),
        ToolExecutionContext(
            flow_id="flow", turn_id="turn", persona_id="chat", state_id="default",
            config_revision="sha256:" + "1" * 64,
        ),
    )
    assert result.outcome == ToolOutcome.EXECUTION_FAILURE
    assert events == ["prepare", "execute", "rollback:token"]


@pytest.mark.asyncio
async def test_write_tool_validates_result_before_commit() -> None:
    events: list[str] = []
    secret = "sk-" + "z" * 32

    class Recoverable:
        def prepare(self, arguments, context):
            events.append("prepare")
            return "token"

        async def execute(self, arguments, context):
            events.append("execute")
            return ToolResult(call_id="host", outcome=ToolOutcome.SUCCESS, data={"value": secret})

        def commit(self, token):
            events.append("commit")

        def rollback(self, token):
            events.append(f"rollback:{token}")

    registry = ToolRegistry()
    registry.register(
        definition("write"), Recoverable(), enabled=True,
        allowed_personas=("chat",), read_only=False,
    )
    result = await ToolExecutor(registry).execute(
        ToolCall(call_id="call-write", name="write", arguments={}),
        ToolExecutionContext(
            flow_id="flow", turn_id="turn", persona_id="chat", state_id="default",
            config_revision="sha256:" + "1" * 64,
        ),
    )
    assert result.outcome == ToolOutcome.INVALID_ARGUMENTS
    assert events == ["prepare", "execute", "rollback:token"]


@pytest.mark.asyncio
async def test_write_tool_cancellation_rolls_back_and_propagates() -> None:
    events: list[str] = []

    class Recoverable:
        def prepare(self, arguments, context):
            events.append("prepare")
            return "token"

        async def execute(self, arguments, context):
            events.append("execute")
            raise asyncio.CancelledError

        def commit(self, token):
            events.append("commit")

        async def rollback(self, token):
            events.append(f"rollback:{token}")

    registry = ToolRegistry()
    registry.register(
        definition("write"), Recoverable(), enabled=True,
        allowed_personas=("chat",), read_only=False,
    )
    with pytest.raises(asyncio.CancelledError):
        await ToolExecutor(registry).execute(
            ToolCall(call_id="call-write", name="write", arguments={}),
            ToolExecutionContext(
                flow_id="flow", turn_id="turn", persona_id="chat", state_id="default",
                config_revision="sha256:" + "1" * 64,
            ),
        )
    assert events == ["prepare", "execute", "rollback:token"]
