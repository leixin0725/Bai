"""[2026-07-19] 自主循环默认零调用；测试 Runner 受全部预算、停止和检查点约束。"""

import asyncio

import pytest

from bai_agent.runtime.loops import (
    AutonomousLoopRunner,
    DisabledLoopPolicy,
    LoopBudget,
    LoopDecision,
    MemoryCheckpointStore,
)


class CountingController:
    def __init__(self, *, cancel=False) -> None:
        self.calls = []
        self.cancel = cancel

    async def run_turn(self, content):
        self.calls.append(content)
        if self.cancel:
            raise asyncio.CancelledError()
        return f"next-{len(self.calls)}"


class ContinuePolicy:
    def next_action(self, state):
        return LoopDecision.CONTINUE


@pytest.mark.asyncio
async def test_disabled_policy_performs_zero_controller_calls() -> None:
    controller = CountingController()
    result = await AutonomousLoopRunner(controller, DisabledLoopPolicy(), LoopBudget.disabled()).run("seed")
    assert result.stop_reason == "disabled"
    assert controller.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("budget", "reason"),
    [
        (LoopBudget(max_iterations=2, deadline_seconds=60, max_tokens=100, max_cost_units=100, tokens_per_iteration=1, cost_per_iteration=1), "max_iterations"),
        (LoopBudget(max_iterations=10, deadline_seconds=60, max_tokens=2, max_cost_units=100, tokens_per_iteration=1, cost_per_iteration=1), "token_budget"),
        (LoopBudget(max_iterations=10, deadline_seconds=60, max_tokens=100, max_cost_units=2, tokens_per_iteration=1, cost_per_iteration=1), "cost_budget"),
    ],
)
async def test_loop_stops_at_configured_budget(budget: LoopBudget, reason: str) -> None:
    result = await AutonomousLoopRunner(CountingController(), ContinuePolicy(), budget).run("seed")
    assert result.stop_reason == reason


@pytest.mark.asyncio
async def test_deadline_manual_stop_cancellation_and_checkpoint_resume() -> None:
    ticks = iter([0.0, 2.0, 2.0])
    deadline = LoopBudget(max_iterations=10, deadline_seconds=1, max_tokens=100, max_cost_units=100, tokens_per_iteration=1, cost_per_iteration=1)
    result = await AutonomousLoopRunner(CountingController(), ContinuePolicy(), deadline, clock=lambda: next(ticks)).run("seed")
    assert result.stop_reason == "deadline"

    stopped = await AutonomousLoopRunner(
        CountingController(), ContinuePolicy(), deadline.model_copy(update={"deadline_seconds": 60}), stop_requested=lambda: True
    ).run("seed")
    assert stopped.stop_reason == "manual_stop"

    with pytest.raises(asyncio.CancelledError):
        await AutonomousLoopRunner(CountingController(cancel=True), ContinuePolicy(), deadline.model_copy(update={"deadline_seconds": 60})).run("seed")

    checkpoints = MemoryCheckpointStore()
    first_controller = CountingController()
    first = await AutonomousLoopRunner(
        first_controller,
        ContinuePolicy(),
        LoopBudget(max_iterations=1, deadline_seconds=60, max_tokens=100, max_cost_units=100, tokens_per_iteration=1, cost_per_iteration=1),
        checkpoint_store=checkpoints,
    ).run("seed", run_id="same")
    second_controller = CountingController()
    second = await AutonomousLoopRunner(
        second_controller,
        ContinuePolicy(),
        LoopBudget(max_iterations=2, deadline_seconds=60, max_tokens=100, max_cost_units=100, tokens_per_iteration=1, cost_per_iteration=1),
        checkpoint_store=checkpoints,
    ).run("seed", run_id="same")
    assert first.iterations == 1
    assert second.iterations == 2
    assert second_controller.calls == ["next-1"]

