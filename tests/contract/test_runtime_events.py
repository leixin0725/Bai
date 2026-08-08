"""[2026-08-08] 定时/系统事件经管道按序投递；事件失败只计数不中断后续处理项。"""

from __future__ import annotations

import asyncio

import pytest

from bai_agent.domain.models import HealthState, PipelineItemKind, TaskStatus
from bai_agent.runtime.pipeline import ProcessingPipeline
from bai_agent.runtime.shell import RuntimeShell
from tests.fakes import DeterministicClock, FakeApplication


@pytest.mark.asyncio
async def test_events_dispatch_in_order_and_failure_does_not_interrupt() -> None:
    app = FakeApplication()
    shell = RuntimeShell(app, clock=DeterministicClock())
    calls: list[tuple[str, int]] = []

    async def handler_fail(payload) -> None:
        calls.append(("fail", payload["n"]))
        raise RuntimeError("事件失败")

    async def handler_ok(payload) -> None:
        calls.append(("ok", payload["n"]))

    shell.register_event_handler("boom", handler_fail)
    shell.register_event_handler("tick", handler_ok)
    pipeline = ProcessingPipeline(shell._handle_item, clock=DeterministicClock())
    worker = asyncio.create_task(pipeline.run())
    await pipeline.submit(PipelineItemKind.TIMER_EVENT, {"event_kind": "boom", "n": 1})
    await pipeline.submit(PipelineItemKind.TIMER_EVENT, {"event_kind": "tick", "n": 2})
    await pipeline.submit(PipelineItemKind.SYSTEM_EVENT, {"event_kind": "tick", "n": 3})
    while shell.status_snapshot().counters["events"] < 3:
        await asyncio.sleep(0.01)
    await pipeline.stop()
    await worker
    assert calls == [("fail", 1), ("ok", 2), ("ok", 3)]
    counters = shell.status_snapshot().counters
    assert counters["events"] == 3
    assert counters["events_failed"] == 1
    assert shell.status_snapshot().health is HealthState.OK


@pytest.mark.asyncio
async def test_shell_task_records_feed_status_and_health_warning() -> None:
    app = FakeApplication()
    shell = RuntimeShell(app, clock=DeterministicClock())
    worker = asyncio.create_task(shell.executor.run())

    async def failing() -> None:
        raise ValueError("后台失败")

    async def success() -> None:
        return None

    failed_id = shell.submit_task("失败任务", failing)
    ok_id = shell.submit_task("成功任务", success)
    while shell.executor.record(failed_id).status is not TaskStatus.FAILURE:
        await asyncio.sleep(0.01)
    await shell.executor.stop()
    await worker
    status = shell.status_snapshot()
    assert status.health is HealthState.WARNING
    assert status.counters["tasks_failed"] == 1
    assert status.counters["tasks_succeeded"] == 1
    assert status.tasks[0].status is TaskStatus.FAILURE
    assert ok_id != failed_id
