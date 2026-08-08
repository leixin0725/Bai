"""[2026-08-08] 真实 PTY 端到端回归：会话常驻 InputReader 与 TUI 共用 stdin 时，
鼠标点击必须全部送达（回归 stdin 双读竞争导致的点击丢失/多次点击）。"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

pytest.importorskip("termios")
pytest.importorskip("tty")
pytest.importorskip("pty")

import fcntl
import os
import pty
import select
import struct
import sys
import termios
import threading
import time
import tty

from textual.widgets import Button

from bai_agent.debug.tui import TextualApprovalPresenter
from bai_agent.domain.models import ContextUsageEstimate
from bai_agent.runtime.input_reader import InputReader, StdinInputSource
from tests.prompt_debug_fakes import FakeAdapter, make_draft


class _FdStream:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd

    def isatty(self) -> bool:
        return True


class _WriterStream(_FdStream):
    def write(self, data: str) -> int:
        return os.write(self._fd, data.encode("utf-8"))

    def flush(self) -> None:
        return


def _open_pty_pair(*, nonblocking_slave: bool = True) -> tuple[int, int]:
    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    tty.setraw(slave, termios.TCSANOW)
    if nonblocking_slave:
        os.set_blocking(slave, False)
    return master, slave


@pytest.mark.asyncio
async def test_tui_mouse_clicks_all_delivered_while_input_reader_active(monkeypatch) -> None:
    in_master, in_slave = _open_pty_pair()
    # 输出端保持阻塞写：排空线程持续运行期间不会 EAGAIN，也避免写入端异常退出。
    err_master, err_slave = _open_pty_pair(nonblocking_slave=False)
    monkeypatch.setattr(sys, "__stdin__", _FdStream(in_slave))
    monkeypatch.setattr(sys, "__stderr__", _WriterStream(err_slave))

    # 排空 stderr PTY，避免 Textual 写入端阻塞。
    drain_stop = threading.Event()

    def drain_stderr() -> None:
        while not drain_stop.is_set():
            try:
                if select.select([err_master], [], [], 0.1)[0]:
                    os.read(err_master, 65536)
            except (OSError, ValueError):
                return

    drainer = threading.Thread(target=drain_stderr, daemon=True)
    drainer.start()

    source = StdinInputSource(_FdStream(in_slave))
    actions: list = []

    async def on_action(action) -> None:
        actions.append(action)

    reader_task = asyncio.create_task(InputReader(source, on_action=on_action).run())
    try:
        adapter = FakeAdapter()
        prepared = adapter.prepare(make_draft("端到端正文"), 1)
        payload = adapter.materialize_sdk_kwargs(prepared)
        estimate = ContextUsageEstimate(
            status="unavailable", max_output_tokens=16, reason="不可估算"
        )
        presenter = TextualApprovalPresenter(color_policy="never", input_source=source)
        decision_task = asyncio.create_task(
            presenter.decide(prepared, payload, estimate, "警告")
        )

        deadline = time.monotonic() + 10
        while True:
            app = presenter.app
            if app is not None and app.display_ready:
                break
            if decision_task.done():
                break
            if time.monotonic() > deadline:
                raise AssertionError("TUI 未在超时内渲染就绪")
            await asyncio.sleep(0.02)

        app = presenter.app
        assert app is not None and app.display_ready
        region = app.query_one("#approve", Button).region
        col = region.x + region.width // 2 + 1
        row = region.y + region.height // 2 + 1
        click = f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m".encode("ascii")
        for _ in range(20):
            os.write(in_master, click)
            await asyncio.sleep(0.02)

        decision = await asyncio.wait_for(decision_task, 10)
        assert decision.decision.value == "approve"
        # 暂停期间 InputReader 不应把任何终端事件当作聊天输入。
        assert actions == []
    finally:
        reader_task.cancel()
        with suppress(asyncio.CancelledError):
            await reader_task
        drain_stop.set()
        for fd in (in_master, in_slave, err_master, err_slave):
            try:
                os.close(fd)
            except OSError:
                pass
