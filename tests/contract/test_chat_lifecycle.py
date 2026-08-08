"""[2026-08-08] 运行时外壳生命周期：EOF、停止信号、排队丢弃与 pending 恢复。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bai_agent.domain.models import SessionState
from bai_agent.runtime.shell import RuntimeShell
from tests.fakes import FakeApplication, FakeInputSource


class BlockingApp(FakeApplication):
    """[2026-08-08] run_turn 阻塞在闸门上，用于验证停止时当前轮语义。"""

    def __init__(self, pending=None) -> None:
        super().__init__(pending=pending)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run_turn(self, content, **kwargs):
        self.calls.append((content, kwargs))
        self.started.set()
        await self.release.wait()
        return "ok"


@pytest.mark.asyncio
async def test_shell_eof_exits_zero_without_turns() -> None:
    app = FakeApplication()
    shell = RuntimeShell(app)
    assert await shell.run(FakeInputSource([], is_tty=True)) == 0
    assert app.calls == []
    assert shell.session_state is SessionState.STOPPING


@pytest.mark.asyncio
async def test_shell_sigint_finishes_current_turn_then_exits_130() -> None:
    app = BlockingApp()
    shell = RuntimeShell(app)
    run_task = asyncio.create_task(shell.run(FakeInputSource(["第一轮"], is_tty=True)))
    await asyncio.wait_for(app.started.wait(), timeout=1)
    shell.request_stop("sigint")
    app.release.set()
    assert await asyncio.wait_for(run_task, timeout=1) == 130
    assert app.calls == [("第一轮", {"reload_config": False})]


@pytest.mark.asyncio
async def test_shell_stop_drops_queued_input_but_keeps_current_turn() -> None:
    app = BlockingApp()
    shell = RuntimeShell(app)
    run_task = asyncio.create_task(
        shell.run(FakeInputSource(["第一轮", "第二轮"], is_tty=True))
    )
    await asyncio.wait_for(app.started.wait(), timeout=1)
    shell.request_stop("sigint")
    app.release.set()
    assert await asyncio.wait_for(run_task, timeout=1) == 130
    assert [content for content, _ in app.calls] == ["第一轮"]


@pytest.mark.asyncio
async def test_shell_resume_pending_runs_first_through_pipeline() -> None:
    pending = SimpleNamespace(turn_id="turn-pending", content="旧正文")
    app = FakeApplication(pending=pending)
    shell = RuntimeShell(app)
    assert (
        await shell.run(
            FakeInputSource([], is_tty=True),
            resume=("旧正文", "turn-pending"),
        )
        == 0
    )
    assert app.calls == [
        ("旧正文", {"reload_config": False, "resume_pending": True, "turn_id": "turn-pending"})
    ]


@pytest.mark.asyncio
async def test_shell_rejected_turn_continues_but_interrupt_stops() -> None:
    from bai_agent.domain.errors import TurnInterrupted, TurnRejected

    class RejectApp(FakeApplication):
        def __init__(self, interrupted: bool) -> None:
            super().__init__()
            self.interrupted = interrupted

        async def run_turn(self, content, **kwargs):
            raise TurnInterrupted() if self.interrupted else TurnRejected()

    app = RejectApp(interrupted=False)
    shell = RuntimeShell(app)
    assert await shell.run(FakeInputSource(["正文"], is_tty=True)) == 0
    assert app.calls == []

    app = RejectApp(interrupted=True)
    shell = RuntimeShell(app)
    with pytest.raises(TurnInterrupted):
        await shell.run(FakeInputSource(["正文"], is_tty=True))
