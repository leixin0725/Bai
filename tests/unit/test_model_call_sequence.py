"""[2026-07-20] 网关身份分配器独占轮内序号并拒绝重复、覆盖和越序。"""

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.model_calls.gateway import CallIdentityAllocator
from tests.prompt_debug_fakes import make_draft


def test_allocator_ignores_caller_sequence_and_assigns_monotonically() -> None:
    allocator = CallIdentityAllocator()
    first = allocator.assign(make_draft(sequence=99))
    second = allocator.assign(make_draft(sequence=2, purpose="tool_continuation"))
    assert first.call_sequence == 1
    assert second.call_sequence == 2


def test_allocator_blocks_duplicate_call_id_and_sequence_override() -> None:
    allocator = CallIdentityAllocator()
    allocator.assign(make_draft(sequence=1))
    with pytest.raises(BaiError, match="调用身份"):
        allocator.assign(make_draft(sequence=1))
