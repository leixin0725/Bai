"""[2026-07-20] 第二 provider 仅实现三方法即可复用批准、重试和释放语义。"""

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


@pytest.mark.asyncio
async def test_second_provider_retries_with_new_approval() -> None:
    adapter = FakeAdapter(failures=[BaiError("PROVIDER_FAILED", "失败。", retryable=True)])
    presenter = FakePresenter()
    gateway = ModelCallGateway(adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator(), max_attempts=2, backoff_seconds=0)
    await gateway.complete(make_draft())
    assert presenter.decisions == [("call-1", 1), ("call-1", 2)]
    assert adapter.materialize_count == 2 and len(adapter.sent) == 2
