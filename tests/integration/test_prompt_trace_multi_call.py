"""[2026-07-20] 多调用按 curation、chat、tool、retry 顺序逐项批准且互不合并。"""

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


@pytest.mark.asyncio
async def test_multi_call_sequence_and_retry_attempt_are_distinct() -> None:
    adapter = FakeAdapter(failures=[BaiError("PROVIDER_FAILED", "失败。", retryable=True)])
    presenter = FakePresenter()
    gateway = ModelCallGateway(adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator(), max_attempts=2, backoff_seconds=0)
    await gateway.complete(make_draft(sequence=7, purpose="memory_curation"))
    await gateway.complete(make_draft(sequence=8, purpose="chat"))
    await gateway.complete(make_draft(sequence=9, purpose="tool_continuation"))
    assert presenter.decisions == [
        ("call-7", 1), ("call-7", 2), ("call-8", 1), ("call-9", 1)
    ]
    assert [state.status for state in gateway.call_states if state.status in {"provider_failed", "completed"}] == [
        "provider_failed", "completed", "completed", "completed"
    ]
