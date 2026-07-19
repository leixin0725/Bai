"""[2026-07-19] 自主循环默认停止；测试 Runner 复用同一 Controller 并受所有预算约束。"""

from __future__ import annotations

import asyncio
from enum import StrEnum
import json
from pathlib import Path
from time import monotonic

from pydantic import Field

from bai_agent.domain.models import FrozenModel
from bai_agent.memory.recovery import atomic_write


class LoopDecision(StrEnum):
    STOP = "stop"
    CONTINUE = "continue"


class LoopBudget(FrozenModel):
    max_iterations: int = Field(ge=0)
    deadline_seconds: float = Field(ge=0)
    max_tokens: int = Field(ge=0)
    max_cost_units: float = Field(ge=0)
    tokens_per_iteration: int = Field(ge=0)
    cost_per_iteration: float = Field(ge=0)

    @classmethod
    def disabled(cls) -> "LoopBudget":
        return cls(
            max_iterations=0,
            deadline_seconds=0,
            max_tokens=0,
            max_cost_units=0,
            tokens_per_iteration=0,
            cost_per_iteration=0,
        )


class LoopResult(FrozenModel):
    run_id: str
    iterations: int
    consumed_tokens: int
    consumed_cost_units: float
    stop_reason: str


class LoopCheckpoint(FrozenModel):
    run_id: str
    iterations: int
    consumed_tokens: int
    consumed_cost_units: float
    next_input: str


class DisabledLoopPolicy:
    def next_action(self, state) -> LoopDecision:
        return LoopDecision.STOP


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self.values: dict[str, LoopCheckpoint] = {}

    def load(self, run_id: str) -> LoopCheckpoint | None:
        return self.values.get(run_id)

    def save(self, checkpoint: LoopCheckpoint) -> None:
        self.values[checkpoint.run_id] = checkpoint


class JsonCheckpointStore:
    """[2026-07-19] 未来显式启用循环时可用原子 JSON 检查点恢复，不保存凭据。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, run_id: str) -> LoopCheckpoint | None:
        if not self.path.exists():
            return None
        value = LoopCheckpoint.model_validate_json(self.path.read_text(encoding="utf-8"))
        return value if value.run_id == run_id else None

    def save(self, checkpoint: LoopCheckpoint) -> None:
        atomic_write(self.path, (checkpoint.model_dump_json() + "\n").encode("utf-8"))


class AutonomousLoopRunner:
    def __init__(
        self,
        controller,
        policy,
        budget: LoopBudget,
        *,
        clock=monotonic,
        stop_requested=lambda: False,
        checkpoint_store=None,
        tracer=None,
    ) -> None:
        self.controller = controller
        self.policy = policy
        self.budget = budget
        self.clock = clock
        self.stop_requested = stop_requested
        self.checkpoints = checkpoint_store or MemoryCheckpointStore()
        self.tracer = tracer

    async def run(self, initial_input: str, *, run_id: str = "default") -> LoopResult:
        if self.policy.next_action(None) == LoopDecision.STOP:
            return LoopResult(run_id=run_id, iterations=0, consumed_tokens=0, consumed_cost_units=0, stop_reason="disabled")
        checkpoint = self.checkpoints.load(run_id)
        iterations = checkpoint.iterations if checkpoint else 0
        tokens = checkpoint.consumed_tokens if checkpoint else 0
        cost = checkpoint.consumed_cost_units if checkpoint else 0.0
        next_input = checkpoint.next_input if checkpoint else initial_input
        started = self.clock()
        reason = "policy_stop"
        while self.policy.next_action({"iterations": iterations}) == LoopDecision.CONTINUE:
            if self.stop_requested():
                reason = "manual_stop"
                break
            if self.clock() - started >= self.budget.deadline_seconds:
                reason = "deadline"
                break
            if iterations >= self.budget.max_iterations:
                reason = "max_iterations"
                break
            if tokens + self.budget.tokens_per_iteration > self.budget.max_tokens:
                reason = "token_budget"
                break
            if cost + self.budget.cost_per_iteration > self.budget.max_cost_units:
                reason = "cost_budget"
                break
            try:
                next_input = await self.controller.run_turn(next_input)
            except asyncio.CancelledError:
                self.checkpoints.save(
                    LoopCheckpoint(
                        run_id=run_id,
                        iterations=iterations,
                        consumed_tokens=tokens,
                        consumed_cost_units=cost,
                        next_input=next_input,
                    )
                )
                raise
            iterations += 1
            tokens += self.budget.tokens_per_iteration
            cost += self.budget.cost_per_iteration
            self.checkpoints.save(
                LoopCheckpoint(
                    run_id=run_id,
                    iterations=iterations,
                    consumed_tokens=tokens,
                    consumed_cost_units=cost,
                    next_input=next_input,
                )
            )
        if self.tracer:
            self.tracer.emit(
                "loop.stopped",
                iteration=iterations,
                stop_reason=reason,
                token_budget=tokens,
                cost_budget=cost,
            )
        return LoopResult(
            run_id=run_id,
            iterations=iterations,
            consumed_tokens=tokens,
            consumed_cost_units=cost,
            stop_reason=reason,
        )
