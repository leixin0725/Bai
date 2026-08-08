"""[2026-08-08] BR-006 一次输入动作：管道 EOF 整批、TTY 缓冲连片合并、逐行独立，零等待。"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

pytest.importorskip("termios")
pytest.importorskip("tty")
pytest.importorskip("pty")

import os
import pty
import selectors
import termios
import threading
import time
import tty

from bai_agent.domain.models import ConversationAction, InputBoundary
from bai_agent.runtime.input_reader import InputReader, StdinInputSource
from tests.fakes import FakeInputSource


async def collect(source) -> tuple[list[ConversationAction], int]:
    actions: list[ConversationAction] = []
    eof_calls = 0

    async def on_action(action: ConversationAction) -> None:
        actions.append(action)

    def on_eof() -> None:
        nonlocal eof_calls
        eof_calls += 1

    await InputReader(source, on_action=on_action, on_eof=on_eof).run()
    return actions, eof_calls


async def test_pipe_input_is_one_action_at_eof() -> None:
    actions, eof_calls = await collect(
        FakeInputSource(["第一行", "第二行", "第三行"], is_tty=False)
    )
    assert len(actions) == 1
    assert actions[0].text == "第一行\n第二行\n第三行"
    assert actions[0].source_boundary is InputBoundary.PIPE_EOF
    assert eof_calls == 1


async def test_tty_sequential_lines_are_independent_actions() -> None:
    actions, _ = await collect(
        FakeInputSource(["第一行", "第二行"], is_tty=True, buffered_indexes=set())
    )
    assert [action.text for action in actions] == ["第一行", "第二行"]
    assert all(
        action.source_boundary is InputBoundary.BUFFER_EMPTY for action in actions
    )


async def test_tty_buffered_paste_merges_until_buffer_empty() -> None:
    actions, _ = await collect(
        FakeInputSource(
            ["第一行", "第二行", "第三行"],
            is_tty=True,
            buffered_indexes={0, 1},
        )
    )
    assert len(actions) == 1
    assert actions[0].text == "第一行\n第二行\n第三行"
    assert actions[0].source_boundary is InputBoundary.BUFFER_EMPTY


async def test_blank_lines_are_preserved_inside_action() -> None:
    actions, _ = await collect(
        FakeInputSource(["第一段", "", "第二段"], is_tty=False)
    )
    assert len(actions) == 1
    assert actions[0].text == "第一段\n\n第二段"


async def test_whitespace_only_input_produces_no_action() -> None:
    actions, eof_calls = await collect(
        FakeInputSource(["", "   "], is_tty=False)
    )
    assert actions == []
    assert eof_calls == 1


@pytest.mark.asyncio
async def test_reader_zero_wait_never_sleeps() -> None:
    """[2026-08-08] 合并只依赖缓冲判定；即使缓冲判定全为 False 也不等待。"""
    started = asyncio.get_running_loop().time()
    actions, _ = await collect(
        FakeInputSource(["甲", "乙"], is_tty=True, buffered_indexes=set())
    )
    assert len(actions) == 2
    assert asyncio.get_running_loop().time() - started < 0.1


class _PtyStream:
    """[2026-08-08] 让 StdinInputSource 复用测试 PTY 从端 fd。"""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd

    def isatty(self) -> bool:
        return True


def _open_raw_pty() -> tuple[int, int]:
    master, slave = pty.openpty()
    tty.setraw(slave, termios.TCSANOW)
    os.set_blocking(slave, False)
    return master, slave


def _collect_with_select(fd: int, expected_bytes: int, timeout: float = 3.0) -> list[bytes]:
    """[2026-08-08] 模拟 Textual 驱动输入线程：select + os.read 独占消费。"""
    chunks: list[bytes] = []
    selector = selectors.SelectSelector()
    selector.register(fd, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and sum(len(chunk) for chunk in chunks) < expected_bytes:
            if selector.select(0.02):
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                chunks.append(chunk)
    finally:
        selector.close()
    return chunks


@pytest.mark.asyncio
async def test_pause_releases_stdin_to_exclusive_reader_then_resume_restores() -> None:
    """[2026-08-08] TUI 独占期间 add_reader 必须让位：暂停时另一读取者收全事件，
    恢复后 shell 输入继续可用（回归 stdin 双读竞争）。"""
    master, slave = _open_raw_pty()
    source = StdinInputSource(_PtyStream(slave))
    click = b"\x1b[<0;30;10M\x1b[<0;30;10m"
    read_task = asyncio.create_task(source.read_line())
    try:
        await asyncio.sleep(0)
        await source.pause()
        await asyncio.sleep(0)  # 让挂起的读协程移除 add_reader
        chunks: list[bytes] = []
        worker = threading.Thread(
            target=lambda: chunks.extend(_collect_with_select(slave, 30 * len(click)))
        )
        worker.start()
        for _ in range(30):
            os.write(master, click)
            await asyncio.sleep(0.005)
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert sum(len(chunk) for chunk in chunks) == 30 * len(click)
        assert not read_task.done()

        await source.resume()
        os.write(master, "继续\n".encode("utf-8"))
        assert await asyncio.wait_for(asyncio.shield(read_task), 2) == "继续\n"
    finally:
        if not read_task.done():
            read_task.cancel()
            with suppress(asyncio.CancelledError):
                await read_task
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_pause_during_pending_read_preserves_buffer_and_resumes() -> None:
    """[2026-08-08] 已读入缓冲的字节保留；暂停期间写入的数据留在内核缓冲，
    恢复后按原合并语义返回完整行。"""
    master, slave = _open_raw_pty()
    source = StdinInputSource(_PtyStream(slave))
    read_task = asyncio.create_task(source.read_line())
    try:
        os.write(master, "A".encode("utf-8"))
        for _ in range(100):
            if bytes(source._buffer) == b"A" or read_task.done():
                break
            await asyncio.sleep(0.01)
        assert bytes(source._buffer) == b"A"

        await source.pause()
        await asyncio.sleep(0)
        os.write(master, "B".encode("utf-8"))
        await asyncio.sleep(0.05)
        assert not read_task.done()

        await source.resume()
        os.write(master, "\n".encode("utf-8"))
        assert await asyncio.wait_for(asyncio.shield(read_task), 2) == "AB\n"
    finally:
        if not read_task.done():
            read_task.cancel()
            with suppress(asyncio.CancelledError):
                await read_task
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_pause_resume_are_idempotent_without_fd() -> None:
    """[2026-08-08] 无 fd（Windows 回退路径）时 pause/resume 保持无副作用幂等。"""
    source = StdinInputSource(_PtyStream(-1))
    await source.pause()
    await source.pause()
    await source.resume()
    await source.resume()
    assert not source._paused
