"""[2026-07-20] 规模覆盖证明批准与 200+ 次混合物理出站严格一一对应。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from bai_agent.domain.errors import BaiError, TurnRejected
from bai_agent.domain.models import ApprovalDecision, CompletionResult
from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, UnavailableEstimator, make_draft


@dataclass
class ScaleAdapter(FakeAdapter):
    retry_sequences: set[int] = field(default_factory=set)
    purpose_by_call: dict[str, str] = field(default_factory=dict)
    outbound: list[tuple[str, int, str]] = field(default_factory=list)

    def prepare(self, draft, attempt):
        self.purpose_by_call[draft.call_id] = draft.purpose
        return super().prepare(draft, attempt)

    async def send_once(self, payload):
        item = (payload.call_id, payload.attempt, self.purpose_by_call[payload.call_id])
        self.outbound.append(item)
        sequence = int(payload.call_id.removeprefix("call-"))
        if sequence in self.retry_sequences and payload.attempt == 1:
            raise BaiError("PROVIDER_UNAVAILABLE", "暂时不可用。", retryable=True)
        return CompletionResult(
            text="完成", finish_reason="stop",
            usage={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        )


@dataclass
class ScalePresenter:
    rejected_sequences: set[int]
    approvals: list[tuple[str, int, str]] = field(default_factory=list)
    rejections: list[tuple[str, int]] = field(default_factory=list)
    request = None
    payload = None

    async def decide(self, request, payload, estimate, warning):
        self.request, self.payload = request, payload
        sequence = int(payload.call_id.removeprefix("call-"))
        if sequence in self.rejected_sequences:
            self.rejections.append((payload.call_id, payload.attempt))
            return ApprovalDecision.reject(payload)
        self.approvals.append((payload.call_id, payload.attempt, request.purpose))
        return ApprovalDecision.approve(payload)

    def clear(self) -> None:
        self.request = None
        self.payload = None


@pytest.mark.asyncio
async def test_two_hundred_mixed_calls_have_exact_approval_outbound_bijection() -> None:
    purposes = ("chat", "memory_curation", "tool_continuation", "future_persona", "retry_probe")
    retry_sequences = {value for value in range(1, 201) if value % 9 == 0}
    rejected_sequences = {value for value in range(1, 201) if value % 17 == 0}
    adapter = ScaleAdapter(retry_sequences=retry_sequences)
    presenter = ScalePresenter(rejected_sequences)
    gateway = ModelCallGateway(
        adapter, debug_enabled=True, presenter=presenter,
        estimator=UnavailableEstimator(), max_attempts=2, backoff_seconds=0,
    )

    for sequence in range(1, 201):
        purpose = purposes[(sequence - 1) % len(purposes)]
        draft = make_draft(f"规模输入-{sequence}", sequence=sequence, purpose=purpose)
        if purpose == "future_persona":
            draft = draft.model_copy(update={"persona_id": "future_persona"})
        if sequence in rejected_sequences:
            with pytest.raises(TurnRejected):
                await gateway.complete(draft)
        else:
            await gateway.complete(draft)

    assert len({item[0] for item in presenter.approvals}) >= 189
    assert presenter.approvals == adapter.outbound
    assert all(call_id not in {item[0] for item in adapter.outbound} for call_id, _ in presenter.rejections)
    assert {item[2] for item in adapter.outbound} == set(purposes)
    expected_prepared = [
        (sequence, attempt)
        for sequence in range(1, 201)
        for attempt in (
            (1, 2)
            if sequence in retry_sequences and sequence not in rejected_sequences
            else (1,)
        )
    ]
    assert [
        (item.call_sequence, item.attempt)
        for item in gateway.call_states if item.status == "prepared"
    ] == expected_prepared
