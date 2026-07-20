"""[2026-07-20] 所有用途和 retry 都通过同一网关形成独立批准项。"""

import pytest

from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


@pytest.mark.asyncio
async def test_all_call_purposes_have_one_approval_per_physical_attempt() -> None:
    presenter = FakePresenter()
    adapter = FakeAdapter()
    gateway = ModelCallGateway(adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator())
    for sequence, purpose in enumerate(("memory_curation", "chat", "tool_continuation", "future_persona"), 1):
        await gateway.complete(make_draft(sequence=sequence, purpose=purpose))
    assert presenter.decisions == [(f"call-{index}", 1) for index in range(1, 5)]
    assert len(adapter.sent) == 4
