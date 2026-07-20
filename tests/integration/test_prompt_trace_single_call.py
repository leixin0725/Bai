"""[2026-07-20] 单次调用端到端验证来源、批准、发送和释放顺序。"""

import pytest

from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


@pytest.mark.asyncio
async def test_single_call_trace_reaches_fake_provider_once() -> None:
    adapter, presenter = FakeAdapter(), FakePresenter()
    await ModelCallGateway(adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator()).complete(make_draft("验收-运行时来源"))
    assert len(adapter.sent) == 1
    assert presenter.decisions == [("call-1", 1)]
    assert adapter.sent[0].sdk_kwargs["messages"][0]["content"] == "验收-运行时来源"
