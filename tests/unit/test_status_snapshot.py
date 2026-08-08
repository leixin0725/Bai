"""[2026-08-08] BR-007 状态快照与真实状态一致：会话状态、队列、任务、计数无重复。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bai_agent.domain.models import SessionState
from bai_agent.domain.models import PipelineItemKind
from bai_agent.runtime.shell import RuntimeShell
from tests.fakes import FakeApplication, FakeInputSource


@pytest.mark.asyncio
async def test_status_after_successful_run_reflects_turns_and_stopping() -> None:
    app = FakeApplication()
    shell = RuntimeShell(app)
    assert await shell.run(FakeInputSource(["一", "二"], is_tty=True)) == 0
    status = shell.status_snapshot()
    assert status.session_state is SessionState.STOPPING
    assert status.queue_depth == 0
    assert status.current_item_id is None
    assert status.counters["chat_turns"] == 2
    assert status.pending_turn_id is None


@pytest.mark.asyncio
async def test_status_during_processing_reports_current_item() -> None:
    app = FakeApplication()
    shell = RuntimeShell(app)
    gate = asyncio.Event()
    release = asyncio.Event()

    async def blocking(content, **kwargs):
        gate.set()
        await release.wait()

    app.run_turn = blocking  # type: ignore[method-assign]
    worker = asyncio.create_task(shell.pipeline.run())
    await shell.pipeline.submit(
        PipelineItemKind.CHAT_INPUT,
        {
            "text": "处理中",
            "source_boundary": "buffer_empty",
            "resume_pending": False,
            "turn_id": None,
        },
    )
    await asyncio.wait_for(gate.wait(), timeout=1)
    status = shell.status_snapshot()
    assert status.session_state is SessionState.PROCESSING
    assert status.current_item_id is not None
    assert status.queue_depth == 0
    release.set()
    await shell.pipeline.stop()
    await worker


@pytest.mark.asyncio
async def test_status_reports_pending_turn_id() -> None:
    pending = SimpleNamespace(turn_id="turn-pending", content="旧正文")
    app = FakeApplication(pending=pending)
    shell = RuntimeShell(app)
    await shell.run(FakeInputSource([], is_tty=True))
    assert shell.status_snapshot().pending_turn_id == "turn-pending"


@pytest.mark.asyncio
async def test_status_counters_match_records_without_duplication() -> None:
    app = FakeApplication()
    shell = RuntimeShell(app)
    await shell.run(FakeInputSource(["一次"], is_tty=True))
    first = shell.status_snapshot().counters["chat_turns"]
    # [2026-08-08] 同一快照重复读取不改变计数；不同事件各自只计一次。
    assert shell.status_snapshot().counters["chat_turns"] == first == 1
