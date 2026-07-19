"""[2026-07-19] 可替换端口仅使用领域 DTO，并保持运行时结构契约。"""

from typing import get_type_hints

from bai_agent.domain.models import CompletionRequest, CompletionResult, StateResolutionContext
from bai_agent.domain.ports import LoopPolicy, MemoryRepository, ModelProvider, StateResolver, Tool


def test_port_protocols_expose_required_methods() -> None:
    assert hasattr(ModelProvider, "complete")
    assert hasattr(MemoryRepository, "append_raw")
    assert hasattr(MemoryRepository, "read_all_raw")
    assert hasattr(Tool, "execute")
    assert hasattr(StateResolver, "resolve")
    assert hasattr(LoopPolicy, "next_action")


def test_model_and_state_dtos_round_trip_json() -> None:
    request = CompletionRequest(flow_id="flow-x", turn_id="turn-x", messages=(), tool_definitions=())
    result = CompletionResult(text="完成", finish_reason="stop")
    context = StateResolutionContext(turn_id="turn-x")
    assert CompletionRequest.model_validate_json(request.model_dump_json()) == request
    assert CompletionResult.model_validate_json(result.model_dump_json()) == result
    assert StateResolutionContext.model_validate_json(context.model_dump_json()) == context
    assert "return" in get_type_hints(ModelProvider.complete)
