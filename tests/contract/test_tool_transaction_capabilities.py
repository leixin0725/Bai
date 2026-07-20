"""[2026-07-20] 工具能力测试确保写副作用在可恢复能力验证前为零。"""

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import ToolDefinition
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
