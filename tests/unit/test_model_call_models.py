"""[2026-07-20] 调用值对象测试固定冻结、摘要、来源和用量的安全边界。"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from bai_agent.domain.models import (
    ActualUsageSummary,
    ApprovalDecision,
    CompletionResult,
    MaterializedSendPayload,
    Participation,
    RequestPart,
    SourceKind,
    SourceRef,
    ToolCall,
    ToolHistoryEvent,
    ToolHistoryEventKind,
    ToolOutcome,
    ToolResult,
    TransactionState,
)


def source() -> SourceRef:
    return SourceRef(
        source_kind=SourceKind.RUNTIME,
        source_id="input-1",
        producer="user_input",
        entity_ids=("turn-1",),
    )


def test_materialized_payload_is_deeply_frozen_and_approval_binds_digest() -> None:
    payload = MaterializedSendPayload.create(
        call_id="call-1",
        attempt=1,
        provider_id="fake",
        model="fake-model",
        sdk_kwargs={"model": "fake-model", "messages": [{"role": "user", "content": "你好"}]},
    )
    assert payload.canonical_payload_sha256.startswith("sha256:")
    with pytest.raises(TypeError):
        payload.sdk_kwargs["model"] = "changed"
    decision = ApprovalDecision.approve(payload)
    decision.validate_payload(payload)
    with pytest.raises(ValueError):
        decision.model_copy(update={"attempt": 2}).validate_payload(payload)


def test_request_part_and_actual_usage_have_no_prompt_references() -> None:
    part = RequestPart(
        part_id="p1",
        order=0,
        participation=Participation.INCLUDED,
        trust="user_instruction",
        payload_pointer="/messages/0/content",
        text_span=(0, 2),
        content="你好",
        sources=(source(),),
    )
    assert part.sources[0].project_relative_path is None
    usage = ActualUsageSummary.actual(
        input_tokens=10,
        output_tokens=3,
        context_capacity=100,
        estimated_input_tokens=9,
    )
    assert usage.actual_total_tokens == 13
    assert not ({"prompt", "parts", "sources", "payload"} & set(ActualUsageSummary.model_fields))


def test_transaction_state_has_exact_three_durable_values() -> None:
    assert {item.value for item in TransactionState} == {
        "PREPARED",
        "READY_PENDING",
        "READY_TO_COMMIT",
    }


def test_tool_runtime_times_are_aware_frozen_and_excluded_from_dtos() -> None:
    instant = datetime(2026, 7, 20, tzinfo=timezone.utc)
    completion = CompletionResult(
        text="",
        finish_reason="tool_calls",
        tool_calls=({"call_id": "call-1", "name": "memory_source_query", "arguments": {}},),
        accepted_at=instant,
        origin_call_id="model-call-1",
    )
    result = ToolResult(
        call_id="call-1",
        outcome=ToolOutcome.SUCCESS,
        data={"ok": True},
        completed_at=instant,
        origin_id="tool-result:call-1:stable",
    )
    assert "accepted_at" not in completion.model_dump(mode="json")
    assert "origin_call_id" not in completion.model_dump(mode="json")
    assert "completed_at" not in result.model_dump(mode="json")
    assert "origin_id" not in result.model_dump(mode="json")
    with pytest.raises(ValidationError):
        CompletionResult(text="", finish_reason="tool_calls", accepted_at=datetime(2026, 7, 20))
    with pytest.raises(ValidationError):
        ToolResult(call_id="call-1", outcome=ToolOutcome.SUCCESS, completed_at=datetime(2026, 7, 20))

    event = ToolHistoryEvent(
        event_id="tool-call-batch:model-call-1",
        kind=ToolHistoryEventKind.TOOL_CALL_BATCH,
        occurred_at=instant,
        original_body="",
        role="assistant",
        tool_calls=(ToolCall(call_id="call-1", name="memory_source_query", arguments={}),),
        sources=(source(),),
    )
    assert event.event_id == "tool-call-batch:model-call-1"
    with pytest.raises(ValidationError):
        event.occurred_at = instant
