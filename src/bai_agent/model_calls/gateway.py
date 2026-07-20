"""[2026-07-20] 唯一网关把每次物理模型调用收口到同一来源、批准和安全边界。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from bai_agent.domain.errors import BaiError, DebugPresentationError, TurnRejected
from bai_agent.domain.models import ApprovalValue, ContextUsageEstimate, MaterializedSendPayload, ModelCallDraft
from bai_agent.model_calls.provenance import validate_provenance
from bai_agent.security.credentials import PromptCredentialGuard


class UnavailableTokenEstimator:
    def estimate(self, request, payload) -> ContextUsageEstimate:
        return ContextUsageEstimate(
            status="unavailable",
            max_output_tokens=request.max_output_tokens,
            reason="当前 provider/model 尚无可信 estimator。",
        )


@dataclass(frozen=True, slots=True)
class CallAttemptState:
    turn_id: str
    call_id: str
    call_sequence: int
    purpose: str
    attempt: int
    status: str


class CallIdentityAllocator:
    """[2026-07-20] 轮内逻辑调用序号只由网关分配，调用方给出的编号不被信任。"""

    def __init__(self) -> None:
        self._next_by_turn: dict[str, int] = {}
        self._seen_call_ids: set[str] = set()

    def assign(self, draft: ModelCallDraft) -> ModelCallDraft:
        if draft.call_id in self._seen_call_ids:
            raise BaiError("MODEL_CALL_IDENTITY_INVALID", "调用身份重复、覆盖或越序。")
        sequence = self._next_by_turn.get(draft.turn_id, 1)
        self._next_by_turn[draft.turn_id] = sequence + 1
        self._seen_call_ids.add(draft.call_id)
        return draft.model_copy(update={"call_sequence": sequence})


class ModelCallGateway:
    is_model_call_gateway = True

    def __init__(
        self,
        adapter,
        *,
        debug_enabled: bool = False,
        presenter=None,
        estimator=None,
        credential_guard: PromptCredentialGuard | None = None,
        max_attempts: int = 1,
        backoff_seconds: float = 0.05,
        identity_allocator: CallIdentityAllocator | None = None,
    ) -> None:
        if debug_enabled and presenter is None:
            raise DebugPresentationError("调试已启用但未提供批准界面。")
        self.adapter = adapter
        self.debug_enabled = debug_enabled
        self.presenter = presenter
        self.estimator = estimator or UnavailableTokenEstimator()
        self.credential_guard = credential_guard or PromptCredentialGuard()
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.sender_payload: MaterializedSendPayload | None = None
        self.last_estimate: ContextUsageEstimate | None = None
        self.identity_allocator = identity_allocator or CallIdentityAllocator()
        self.call_states: list[CallAttemptState] = []
        self._serial_lock = asyncio.Lock()

    async def complete(self, draft: ModelCallDraft):
        async with self._serial_lock:
            assigned = self.identity_allocator.assign(draft)
            return await self._complete_assigned(assigned)

    def _record(self, draft: ModelCallDraft, attempt: int, status: str) -> None:
        self.call_states.append(
            CallAttemptState(
                turn_id=draft.turn_id,
                call_id=draft.call_id,
                call_sequence=draft.call_sequence,
                purpose=draft.purpose,
                attempt=attempt,
                status=status,
            )
        )

    async def _complete_assigned(self, draft: ModelCallDraft):
        last_error: BaiError | None = None
        for attempt in range(1, self.max_attempts + 1):
            prepared = self.adapter.prepare(draft, attempt)
            self._record(draft, attempt, "prepared")
            payload = self.adapter.materialize_sdk_kwargs(prepared)
            self._record(draft, attempt, "materialized")
            payload.verify_digest()
            validate_provenance(payload.sdk_kwargs, prepared.parts)
            self.credential_guard.before_display(payload.sdk_kwargs)
            estimate = self.estimator.estimate(prepared, payload)
            self.last_estimate = estimate
            self._record(draft, attempt, "display_ready")
            if self.debug_enabled:
                try:
                    decision = await self.presenter.decide(
                        prepared,
                        payload,
                        estimate,
                        "本地界面可能显示私人记忆；原始追踪不会保存。",
                    )
                except TurnRejected:
                    self.presenter.clear()
                    raise
                except Exception as exc:
                    self.presenter.clear()
                    raise DebugPresentationError() from exc
                if decision.decision != ApprovalValue.APPROVE:
                    self.presenter.clear()
                    self._record(draft, attempt, "rejected")
                    raise TurnRejected()
                decision.validate_payload(payload)
                self._record(draft, attempt, "approved")
                # [2026-07-20] 网络发送前先清除正文与来源；sender 只接管不可变 payload。
                self.presenter.clear()
            self.credential_guard.before_send(payload.sdk_kwargs)
            payload.verify_digest()
            self.sender_payload = payload
            self._record(draft, attempt, "sender_owned")
            try:
                result = await self.adapter.send_once(payload)
                self._record(draft, attempt, "completed")
                return result
            except BaiError as exc:
                last_error = exc
                self._record(draft, attempt, "provider_failed")
                if not exc.retryable or attempt >= self.max_attempts:
                    raise
                if self.backoff_seconds:
                    await asyncio.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
            finally:
                self.sender_payload = None
        assert last_error is not None
        raise last_error


@dataclass
class LegacyProviderAdapter:
    """[2026-07-20] 仅供旧测试替身迁移；生产适配器不得重新暴露 complete 旁路。"""

    provider: Any
    profile: dict[str, Any]

    def prepare(self, draft: ModelCallDraft, attempt: int):
        from bai_agent.domain.models import PreparedProviderRequest

        kwargs = {
            "model": self.profile.get("model", draft.model_profile_id or "test-model"),
            "messages": [item.model_dump(mode="json", exclude={"trust"}, exclude_none=True) for item in draft.request.messages],
        }
        if draft.request.tool_definitions:
            kwargs["tools"] = list(draft.request.tool_definitions)
        return PreparedProviderRequest(
            call_id=draft.call_id, attempt=attempt, provider_id="legacy-test", model=str(kwargs["model"]),
            provider_request=kwargs, max_output_tokens=int(self.profile.get("max_output_tokens", 8192)),
            parts=draft.parts, call_sequence=draft.call_sequence, purpose=draft.purpose,
            turn_id=draft.turn_id, flow_id=draft.flow_id, persona_id=draft.persona_id,
            state_id=draft.state_id, config_revision=draft.config_revision,
        )

    def materialize_sdk_kwargs(self, request):
        from bai_agent.domain.models import MaterializedSendPayload

        return MaterializedSendPayload.create(
            call_id=request.call_id, attempt=request.attempt, provider_id=request.provider_id,
            model=request.model, sdk_kwargs=request.provider_request,
        )

    async def send_once(self, payload):
        from bai_agent.domain.models import CompletionRequest, Message, thaw_json

        kwargs = thaw_json(payload.sdk_kwargs)
        request = CompletionRequest(
            flow_id=payload.call_id,
            turn_id=payload.call_id,
            model_profile_id=str(kwargs.get("model", "")),
            messages=tuple(Message(role=item["role"], content=item.get("content", ""), tool_call_id=item.get("tool_call_id")) for item in kwargs.get("messages", [])),
            tool_definitions=tuple(kwargs.get("tools", [])),
        )
        return await self.provider.complete(request)
