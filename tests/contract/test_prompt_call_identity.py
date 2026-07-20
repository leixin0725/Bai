"""[2026-07-20] TUI 身份字段在 retry 间完整且逻辑调用字段稳定。"""

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


@pytest.mark.asyncio
async def test_identity_fields_complete_and_stable_across_retry() -> None:
    class CapturingPresenter(FakePresenter):
        def __init__(self):
            super().__init__()
            self.identities = []

        async def decide(self, request, payload, estimate, warning):
            self.identities.append((request.turn_id, request.flow_id, request.call_sequence, request.purpose, request.persona_id, request.state_id, request.provider_id, request.model, request.config_revision, request.attempt))
            return await super().decide(request, payload, estimate, warning)

    adapter = FakeAdapter(failures=[BaiError("PROVIDER_FAILED", "失败。", retryable=True)])
    presenter = CapturingPresenter()
    await ModelCallGateway(adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator(), max_attempts=2, backoff_seconds=0).complete(make_draft())
    first, second = presenter.identities
    assert all(value is not None for value in first)
    assert first[:-1] == second[:-1]
    assert (first[-1], second[-1]) == (1, 2)
