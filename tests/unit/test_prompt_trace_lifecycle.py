"""[2026-07-20] 批准后 presenter 与 sender 均按边界释放高敏感对象。"""

import pytest

from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


@pytest.mark.asyncio
async def test_presenter_clears_before_send_and_sender_releases_after_send() -> None:
    adapter, presenter = FakeAdapter(), FakePresenter()
    gateway = ModelCallGateway(adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator())
    await gateway.complete(make_draft("私人正文"))
    assert presenter.request is None and presenter.payload is None
    assert gateway.sender_payload is None and adapter.active_payload is None
