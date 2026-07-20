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

    async def complete(self, draft: ModelCallDraft):
        last_error: BaiError | None = None
        for attempt in range(1, self.max_attempts + 1):
            prepared = self.adapter.prepare(draft, attempt)
            payload = self.adapter.materialize_sdk_kwargs(prepared)
            payload.verify_digest()
            validate_provenance(payload.sdk_kwargs, prepared.parts)
            self.credential_guard.before_display(payload.sdk_kwargs)
            estimate = self.estimator.estimate(prepared, payload)
            self.last_estimate = estimate
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
                    raise TurnRejected()
                decision.validate_payload(payload)
                # [2026-07-20] 网络发送前先清除正文与来源；sender 只接管不可变 payload。
                self.presenter.clear()
            self.credential_guard.before_send(payload.sdk_kwargs)
            payload.verify_digest()
            self.sender_payload = payload
            try:
                return await self.adapter.send_once(payload)
            except BaiError as exc:
                last_error = exc
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
