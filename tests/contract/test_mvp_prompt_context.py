"""[2026-07-19] MVP 每轮上下文包含所有强制段并保持确定顺序。"""

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import StateResolutionResult
from bai_agent.prompting.assembler import PromptAssembler


def test_mvp_context_has_every_required_segment_in_order() -> None:
    assembler = PromptAssembler.mvp("基础人格", ("默认状态",))
    context = assembler.assemble(
        flow_id="flow",
        turn_id="turn",
        config_revision="sha256:" + "1" * 64,
        state_resolution=StateResolutionResult(
            state_id="default",
            ordered_persona_ids=("state_default",),
            resolver_id="static",
            resolver_version="1",
            reason_code="configured_default",
        ),
        memory_overview="尚无长期记忆",
        long_term_memories=(),
        recent_records=(),
        current_input="你好",
    )
    assert [segment.segment_id for segment in context.segments] == [
        "base_persona",
        "state_persona:state_default",
        "memory_overview",
        "long_term_memories",
        "recent_records",
        "current_input",
    ]


def test_missing_required_segment_fails_before_generation() -> None:
    with pytest.raises(BaiError, match="强制提示段"):
        PromptAssembler.mvp("", ("默认状态",))

