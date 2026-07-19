"""[2026-07-19] 测试替身记录调用顺序，并支持确定性响应与故障注入。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from bai_agent.domain.models import CompletionRequest, CompletionResult


@dataclass
class FakeProvider:
    response: str = "测试响应"
    failure: Exception | None = None
    requests: list[CompletionRequest] = field(default_factory=list)

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return CompletionResult(text=self.response, finish_reason="stop")


class DeterministicClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 19, tzinfo=timezone.utc)


@dataclass
class FailureInjector:
    fail_at: str | None = None
    visited: list[str] = field(default_factory=list)

    def hit(self, point: str) -> None:
        self.visited.append(point)
        if point == self.fail_at:
            raise OSError("受控故障")

