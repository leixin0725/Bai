"""[2026-07-20] 提示调试测试替身只记录载荷、批准和释放，不访问网络。"""

from dataclasses import dataclass, field

from bai_agent.domain.models import (
    ApprovalDecision,
    CompletionRequest,
    CompletionResult,
    ContextUsageEstimate,
    MaterializedSendPayload,
    Message,
    ModelCallDraft,
    Participation,
    PreparedProviderRequest,
    RequestPart,
    SourceKind,
    SourceRef,
)


def make_draft(content: str = "你好", *, sequence: int = 1, purpose: str = "chat") -> ModelCallDraft:
    source = SourceRef(
        source_kind=SourceKind.RUNTIME,
        source_id=f"input-{sequence}",
        entity_ids=("turn-1",),
        producer="user_input",
    )
    part = RequestPart(
        part_id=f"part-{sequence}",
        order=0,
        participation=Participation.INCLUDED,
        trust="user_instruction",
        payload_pointer="/messages/0/content",
        text_span=(0, len(content)),
        content=content,
        sources=(source,),
    )
    request = CompletionRequest(
        flow_id="flow-1",
        turn_id="turn-1",
        model_profile_id="chat",
        messages=(Message(role="user", content=content),),
    )
    return ModelCallDraft(
        call_id=f"call-{sequence}", turn_id="turn-1", flow_id="flow-1",
        call_sequence=sequence, purpose=purpose, persona_id="chat", state_id="default",
        config_revision="sha256:" + "1" * 64, model_profile_id="chat",
        request=request, parts=(part,),
    )


@dataclass
class FakeAdapter:
    failures: list[Exception] = field(default_factory=list)
    materialize_count: int = 0
    sent: list[MaterializedSendPayload] = field(default_factory=list)
    active_payload: MaterializedSendPayload | None = None

    def prepare(self, draft: ModelCallDraft, attempt: int) -> PreparedProviderRequest:
        return PreparedProviderRequest(
            call_id=draft.call_id, attempt=attempt, provider_id="fake", model="fake-model",
            provider_request={"model": "fake-model", "messages": [item.model_dump(mode="json", exclude={"trust"}) for item in draft.request.messages]},
            max_output_tokens=16, parts=draft.parts, call_sequence=draft.call_sequence,
            purpose=draft.purpose, turn_id=draft.turn_id, flow_id=draft.flow_id,
            persona_id=draft.persona_id, state_id=draft.state_id, config_revision=draft.config_revision,
        )

    def materialize_sdk_kwargs(self, request: PreparedProviderRequest) -> MaterializedSendPayload:
        self.materialize_count += 1
        return MaterializedSendPayload.create(
            call_id=request.call_id, attempt=request.attempt, provider_id=request.provider_id,
            model=request.model, sdk_kwargs=request.provider_request,
        )

    async def send_once(self, payload: MaterializedSendPayload) -> CompletionResult:
        self.active_payload = payload
        try:
            self.sent.append(payload)
            if self.failures:
                raise self.failures.pop(0)
            return CompletionResult(text="完成", finish_reason="stop", usage={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3})
        finally:
            self.active_payload = None


@dataclass
class FakePresenter:
    approve: bool = True
    decisions: list[tuple[str, int]] = field(default_factory=list)
    cleared: bool = False
    request = None
    payload = None

    async def decide(self, request, payload, estimate, warning):
        self.request = request
        self.payload = payload
        self.decisions.append((payload.call_id, payload.attempt))
        return ApprovalDecision.approve(payload) if self.approve else ApprovalDecision.reject(payload)

    def clear(self) -> None:
        self.request = None
        self.payload = None
        self.cleared = True


class UnavailableEstimator:
    def estimate(self, request, payload):
        return ContextUsageEstimate(
            status="unavailable", max_output_tokens=request.max_output_tokens,
            reason="测试 provider 没有 estimator",
        )
