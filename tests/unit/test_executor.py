"""[2026-08-08] BR-004 最小后台执行器：串行状态机、失败保留原因、积压顺序。"""

from __future__ import annotations

import asyncio

import pytest

from bai_agent.domain.models import TaskStatus
from bai_agent.runtime.executor import BackgroundExecutor
from tests.fakes import DeterministicClock


async def drain(executor: BackgroundExecutor, worker: asyncio.Task[None]) -> None:
    await executor.stop()
    await worker


async def test_executor_runs_tasks_serially_in_submission_order() -> None:
    executor = BackgroundExecutor(clock=DeterministicClock())
    worker = asyncio.create_task(executor.run())
    order: list[str] = []

    async def task(name: str) -> None:
        order.append(name)

    first = executor.submit("甲", lambda: task("甲"))
    second = executor.submit("乙", lambda: task("乙"))
    third = executor.submit("丙", lambda: task("丙"))
    while len(order) < 3:
        await asyncio.sleep(0.01)
    await drain(executor, worker)
    assert order == ["甲", "乙", "丙"]
    assert executor.record(first).status is TaskStatus.SUCCESS
    assert executor.record(second).status is TaskStatus.SUCCESS
    assert executor.record(third).status is TaskStatus.SUCCESS
    assert executor.record(first).started_at is not None
    assert executor.record(first).finished_at is not None


async def test_executor_never_overlaps_tasks() -> None:
    executor = BackgroundExecutor(clock=DeterministicClock())
    worker = asyncio.create_task(executor.run())
    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def blocking() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.set()
        await release.wait()
        active -= 1

    executor.submit("阻塞", blocking)
    second = executor.submit("排队", lambda: asyncio.sleep(0))
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0.01)
    assert max_active == 1
    assert executor.record(second).status is TaskStatus.WAITING
    release.set()
    while executor.record(second).status is not TaskStatus.SUCCESS:
        await asyncio.sleep(0.01)
    await drain(executor, worker)
    assert max_active == 1


async def test_executor_failure_records_reason_and_continues() -> None:
    executor = BackgroundExecutor(clock=DeterministicClock())
    worker = asyncio.create_task(executor.run())
    order: list[str] = []

    async def failing() -> None:
        raise ValueError("受控失败")

    async def success() -> None:
        order.append("ok")

    failed = executor.submit("失败", failing)
    ok = executor.submit("成功", success)
    while executor.record(failed).status is not TaskStatus.FAILURE or not order:
        await asyncio.sleep(0.01)
    await drain(executor, worker)
    assert executor.record(failed).status is TaskStatus.FAILURE
    assert "受控失败" in (executor.record(failed).error or "")
    assert executor.record(ok).status is TaskStatus.SUCCESS


async def test_executor_stop_lets_queued_tasks_finish() -> None:
    executor = BackgroundExecutor(clock=DeterministicClock())
    worker = asyncio.create_task(executor.run())
    order: list[str] = []

    async def task(name: str) -> None:
        order.append(name)

    executor.submit("一", lambda: task("一"))
    executor.submit("二", lambda: task("二"))
    await drain(executor, worker)
    assert order == ["一", "二"]
