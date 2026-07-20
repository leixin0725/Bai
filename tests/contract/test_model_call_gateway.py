"""[2026-07-20] 网关测试固定唯一物化、摘要批准和 debug 等价性。"""

import pytest

from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


@pytest.mark.asyncio
async def test_gateway_materializes_once_and_approval_does_not_rewrite_payload() -> None:
    adapter = FakeAdapter()
    presenter = FakePresenter()
    gateway = ModelCallGateway(adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator())
    result = await gateway.complete(make_draft())
    assert result.text == "完成"
    assert adapter.materialize_count == 1
    assert presenter.decisions == [("call-1", 1)]
    assert presenter.cleared and adapter.active_payload is None


@pytest.mark.asyncio
async def test_debug_on_and_off_send_equal_payloads() -> None:
    first, second = FakeAdapter(), FakeAdapter()
    await ModelCallGateway(first, debug_enabled=True, presenter=FakePresenter(), estimator=UnavailableEstimator()).complete(make_draft())
    await ModelCallGateway(second, debug_enabled=False, estimator=UnavailableEstimator()).complete(make_draft())
    assert dict(first.sent[0].sdk_kwargs) == dict(second.sent[0].sdk_kwargs)
