"""[2026-07-19] 测试替身记录调用顺序，并支持确定性响应与故障注入。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
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


@dataclass
class FakeInputSource:
    """[2026-08-08] 可注入的输入源：行序列、缓冲连片位置与 TTY/管道标记。"""

    lines: list[str]
    buffered_indexes: set[int] = field(default_factory=set)
    is_tty: bool = True
    _index: int = field(default=0, repr=False)

    async def read_line(self) -> str | None:
        if self._index >= len(self.lines):
            return None
        line = self.lines[self._index]
        self._index += 1
        return line

    async def buffered(self) -> bool:
        return self._index in self.buffered_indexes


@dataclass
class FakeEventRecorder:
    """[2026-08-08] 事件处理记录器，可注入失败以验证失败不中断后续处理项。"""

    calls: list[dict[str, Any]] = field(default_factory=list)
    failure: Exception | None = None

    async def handle(self, payload: dict[str, Any]) -> None:
        self.calls.append(payload)
        if self.failure:
            raise self.failure


@dataclass
class FakeApplication:
    """[2026-08-08] 运行时外壳测试替身；run_turn 记录调用且可注入重载失败。"""

    pending: Any = None
    reload_status: Any = None
    closed: bool = False
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    discards: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.archive = SimpleNamespace(pending_turn=lambda: self.pending)
        if self.reload_status is None:
            from bai_agent.domain.models import ReloadStatus

            self.reload_status = ReloadStatus(revision="sha256:" + "0" * 64, ok=True)

    def reload_config_with_status(self) -> Any:
        return self.reload_status

    async def run_turn(self, content: str, **kwargs: Any) -> str:
        self.calls.append((content, kwargs))
        return "ok"

    def discard_pending(self, expected_turn_id: Any = None) -> Any:
        self.discards.append(expected_turn_id)
        if self.pending is None:
            return None
        return self.pending.turn_id

    def close(self) -> None:
        self.closed = True
