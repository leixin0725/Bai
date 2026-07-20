"""[2026-07-20] 高频批准后正文对象不被 presenter、sender 或数值用量摘要保留。"""

from __future__ import annotations

import gc
import weakref

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import CompletionResult
from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


class DroppingAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.payload_refs: list[weakref.ReferenceType] = []

    def materialize_sdk_kwargs(self, request):
        payload = super().materialize_sdk_kwargs(request)
        self.payload_refs.append(weakref.ref(payload))
        return payload

    async def send_once(self, payload):
        self.active_payload = payload
        try:
            return CompletionResult(
                text="完成", finish_reason="stop",
                usage={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            )
        finally:
            self.active_payload = None


@pytest.mark.asyncio
async def test_one_thousand_approvals_release_all_materialized_payloads() -> None:
    adapter, presenter = DroppingAdapter(), FakePresenter()
    gateway = ModelCallGateway(
        adapter, debug_enabled=True, presenter=presenter,
        estimator=UnavailableEstimator(), backoff_seconds=0,
    )
    for sequence in range(1, 1001):
        await gateway.complete(make_draft(f"私有正文-{sequence}", sequence=sequence))
    gc.collect()
    assert presenter.request is None and presenter.payload is None
    assert gateway.sender_payload is None and adapter.active_payload is None
    assert all(reference() is None for reference in adapter.payload_refs)
    assert len(presenter.decisions) == 1000
    assert gateway.last_actual_usage is not None
    assert not ({"prompt", "parts", "sources", "payload"} & set(type(gateway.last_actual_usage).model_fields))


@pytest.mark.asyncio
async def test_sender_releases_payload_after_failure() -> None:
    adapter, presenter = FakeAdapter(failures=[BaiError("PROVIDER_FAILED", "失败。")]), FakePresenter()
    gateway = ModelCallGateway(
        adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator(),
    )
    with pytest.raises(BaiError):
        await gateway.complete(make_draft("失败也要释放"))
    assert gateway.sender_payload is None and adapter.active_payload is None
    assert presenter.request is None and presenter.payload is None
