"""[2026-07-19] 核心端口只引用领域 DTO，避免供应商与文件实现反向渗透。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from bai_agent.domain.models import (
    ApprovalDecision,
    CompletionRequest,
    CompletionResult,
    ContextUsageEstimate,
    MaterializedSendPayload,
    ModelCallDraft,
    PreparedProviderRequest,
    RawRecord,
    StateResolutionContext,
    StateResolutionResult,
)


class ModelProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResult: ...


class MemoryRepository(Protocol):
    def append_raw(self, record: RawRecord) -> RawRecord: ...

    def read_all_raw(self) -> tuple[RawRecord, ...]: ...


class Tool(Protocol):
    async def execute(self, arguments: dict[str, Any], context: Any) -> Any: ...


class StateResolver(Protocol):
    def resolve(self, context: StateResolutionContext) -> StateResolutionResult: ...


class ConfigSource(Protocol):
    def snapshot(self) -> Any: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """[2026-07-20] 进程默认 UTC 时钟；测试可用同端口确定时钟替换。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class LoopPolicy(Protocol):
    def next_action(self, state: Any) -> Any: ...


class ProviderAdapter(Protocol):
    """[2026-07-20] Provider 仅准备、唯一物化并发送一次；重试属于网关。"""

    def prepare(self, draft: ModelCallDraft, attempt: int) -> PreparedProviderRequest: ...

    def materialize_sdk_kwargs(self, request: PreparedProviderRequest) -> MaterializedSendPayload: ...

    async def send_once(self, payload: MaterializedSendPayload) -> CompletionResult: ...


class ApprovalPresenter(Protocol):
    async def decide(
        self,
        request: PreparedProviderRequest,
        payload: MaterializedSendPayload,
        estimate: ContextUsageEstimate,
        warning: str,
    ) -> ApprovalDecision: ...

    def clear(self) -> None: ...


class TokenEstimator(Protocol):
    def estimate(
        self,
        request: PreparedProviderRequest,
        payload: MaterializedSendPayload,
    ) -> ContextUsageEstimate: ...


class ModelCallGateway(Protocol):
    async def complete(self, draft: ModelCallDraft) -> CompletionResult: ...


class TurnUnitOfWorkPort(Protocol):
    def begin(self, checkpoint: Any, provisional_user_record: RawRecord) -> None: ...

    def discard(self) -> None: ...

    def pending(self, failure_code: str) -> None: ...

    def ready(self, assistant_record: RawRecord, target_long_term_document: Any | None = None) -> None: ...

    def commit(self) -> None: ...


class RecoverableWriteTool(Protocol):
    def prepare(self, arguments: dict[str, Any], context: Any) -> Any: ...

    def commit(self, prepared: Any) -> Any: ...

    def rollback(self, prepared: Any) -> None: ...


class CompensatingWriteTool(Protocol):
    compensation_contract: str

    def compensate(self, result: Any, context: Any) -> None: ...
