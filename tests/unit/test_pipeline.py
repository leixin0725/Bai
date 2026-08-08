"""[2026-08-08] 串行管道：顺序、防重入、恰好一次与停止语义。"""

from __future__ import annotations

import asyncio

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import PipelineItemKind
from bai_agent.runtime.pipeline import ProcessingPipeline
from tests.fakes import DeterministicClock


async def test_pipeline_processes_items_serially_in_submission_order() -> None:
    handled: list[str] = []
    done = asyncio.Event()

    async def handler(item) -> None:
        handled.append(item.item_id)
        if len(handled) == 3:
            done.set()

    pipeline = ProcessingPipeline(handler, clock=DeterministicClock())
    worker = asyncio.create_task(pipeline.run())
    first = await pipeline.submit(
        PipelineItemKind.CHAT_INPUT, {"text": "一", "source_boundary": "buffer_empty"}
    )
    second = await pipeline.submit(PipelineItemKind.TIMER_EVENT, {"event_kind": "tick"})
    third = await pipeline.submit(PipelineItemKind.SYSTEM_EVENT, {"event_kind": "notice"})
    await asyncio.wait_for(done.wait(), timeout=1)
    await pipeline.stop()
    await worker
    assert handled == [first.item_id, second.item_id, third.item_id]
    assert first.sequence < second.sequence < third.sequence


async def test_pipeline_never_processes_two_items_concurrently() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    active = 0
    max_active = 0
    handled: list[int] = []

    async def handler(item) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.set()
        await release.wait()
        active -= 1
        handled.append(item.sequence)
        if len(handled) == 2:
            finished.set()

    pipeline = ProcessingPipeline(handler, clock=DeterministicClock())
    worker = asyncio.create_task(pipeline.run())
    await pipeline.submit(PipelineItemKind.CHAT_INPUT, {"text": "甲", "source_boundary": "buffer_empty"})
    await pipeline.submit(PipelineItemKind.CHAT_INPUT, {"text": "乙", "source_boundary": "buffer_empty"})
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0.01)
    assert max_active == 1
    assert handled == []
    release.set()
    await asyncio.wait_for(finished.wait(), timeout=1)
    await pipeline.stop()
    await worker
    assert handled == [1, 2]


async def test_pipeline_failure_propagates_and_is_not_retried() -> None:
    handled: list[int] = []

    async def handler(item) -> None:
        if item.sequence == 2:
            raise RuntimeError("受控失败")
        handled.append(item.sequence)

    pipeline = ProcessingPipeline(handler, clock=DeterministicClock())
    worker = asyncio.create_task(pipeline.run())
    await pipeline.submit(PipelineItemKind.CHAT_INPUT, {"text": "甲", "source_boundary": "buffer_empty"})
    await pipeline.submit(PipelineItemKind.CHAT_INPUT, {"text": "乙", "source_boundary": "buffer_empty"})
    with pytest.raises(RuntimeError, match="受控失败"):
        await worker
    assert handled == [1]


async def test_pipeline_stop_drains_queued_items_and_rejects_new_submissions() -> None:
    gate = asyncio.Event()
    release = asyncio.Event()
    handled: list[int] = []

    async def handler(item) -> None:
        if item.sequence == 1:
            gate.set()
            await release.wait()
        handled.append(item.sequence)

    pipeline = ProcessingPipeline(handler, clock=DeterministicClock())
    worker = asyncio.create_task(pipeline.run())
    await pipeline.submit(PipelineItemKind.CHAT_INPUT, {"text": "甲", "source_boundary": "buffer_empty"})
    await asyncio.wait_for(gate.wait(), timeout=1)
    await pipeline.submit(PipelineItemKind.CHAT_INPUT, {"text": "乙", "source_boundary": "buffer_empty"})
    await pipeline.stop()
    release.set()
    await worker
    assert handled == [1]
    with pytest.raises(BaiError) as caught:
        await pipeline.submit(PipelineItemKind.CHAT_INPUT, {"text": "丙", "source_boundary": "buffer_empty"})
    assert caught.value.code == "RUNTIME_STOPPING"
