"""[2026-07-19] 替换 StateResolver 不要求 Controller、MemoryRepository 或 PromptAssembler 改签名。"""

import inspect

import pytest
from pydantic import ValidationError

from bai_agent.domain.models import AgentStateDefinition, StateResolutionContext, StateResolutionResult
from bai_agent.runtime.controller import SingleTurnController


def test_agent_state_definition_rejects_duplicate_personas() -> None:
    with pytest.raises(ValidationError):
        AgentStateDefinition(
            state_id="focused",
            ordered_persona_ids=("state_a", "state_a"),
            enabled=True,
        )
    assert AgentStateDefinition(state_id="bare", ordered_persona_ids=(), enabled=True).state_id == "bare"


def test_replacement_resolver_satisfies_same_structural_contract() -> None:
    class Replacement:
        def resolve(self, context: StateResolutionContext) -> StateResolutionResult:
            return StateResolutionResult(
                state_id="focused",
                ordered_persona_ids=("state_focused",),
                resolver_id="replacement",
                resolver_version="1",
                reason_code="test",
            )

    assert Replacement().resolve(StateResolutionContext(turn_id="turn")).state_id == "focused"
    parameters = inspect.signature(SingleTurnController.__init__).parameters
    assert "state_resolver" in parameters
    assert "repository" in parameters
    assert "prompt_assembler" in parameters
