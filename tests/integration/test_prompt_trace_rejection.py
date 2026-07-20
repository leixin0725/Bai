"""[2026-07-20] 明确拒绝不发送且不复用普通失败 pending。"""

import pytest

from bai_agent.domain.errors import TurnRejected
from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


@pytest.mark.asyncio
async def test_reject_sends_zero_and_clears_presenter() -> None:
    adapter, presenter = FakeAdapter(), FakePresenter(approve=False)
    gateway = ModelCallGateway(adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator())
    with pytest.raises(TurnRejected):
        await gateway.complete(make_draft())
    assert adapter.sent == [] and presenter.cleared
