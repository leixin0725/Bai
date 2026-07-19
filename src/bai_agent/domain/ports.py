"""[2026-07-19] 核心端口只引用领域 DTO，避免供应商与文件实现反向渗透。"""

from __future__ import annotations

from typing import Any, Protocol

from bai_agent.domain.models import (
    CompletionRequest,
    CompletionResult,
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
    def now(self) -> Any: ...


class LoopPolicy(Protocol):
    def next_action(self, state: Any) -> Any: ...

