"""[2026-08-10] TTY 行编辑器：Enter 发送、Shift+Enter/Ctrl+J 换行、粘贴不回显标记。"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

pytest.importorskip("termios")
pytest.importorskip("tty")
pytest.importorskip("pty")

import os
import pty
import termios
import tty

from bai_agent.runtime.tty_input import (
    DISABLE_PROTOCOLS,
    ENABLE_PROTOCOLS,
    TtyLineEditor,
)


class _FdStream:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd


class _CaptureWriter:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, text: str) -> None:
        self.writes.append(text)

    def flush(self) -> None:
        return None

    @property
    def text(self) -> str:
        return "".join(self.writes)


def _open_pty() -> tuple[int, int]:
    master, slave = pty.openpty()
    return master, slave


async def _read(editor: TtyLineEditor, master: int, payload: bytes) -> str | None:
    task = asyncio.create_task(editor.read_line())
    await asyncio.sleep(0)
    os.write(master, payload)
    return await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_enter_submits_line_and_echoes_visible_text() -> None:
    master, slave = _open_pty()
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        assert await _read(editor, master, "你好".encode("utf-8") + b"\r") == "你好"
        assert "你好" in writer.text
        assert "\r\n" in writer.text
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_shift_enter_inserts_newline_without_marker_echo() -> None:
    master, slave = _open_pty()
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        task = asyncio.create_task(editor.read_line())
        await asyncio.sleep(0)
        os.write(master, "第一行".encode("utf-8"))
        os.write(master, b"\x1b[13;2u")
        os.write(master, "第二行".encode("utf-8"))
        os.write(master, b"\r")
        assert await asyncio.wait_for(task, timeout=1) == "第一行\n第二行"
        assert "200~" not in writer.text
        assert "201~" not in writer.text
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_ctrl_j_is_newline_fallback() -> None:
    master, slave = _open_pty()
    editor = TtyLineEditor(_FdStream(slave), _CaptureWriter())
    try:
        assert await _read(editor, master, b"a\nb\r") == "a\nb"
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_paste_inserts_content_without_markers_and_without_autosubmit() -> None:
    master, slave = _open_pty()
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        task = asyncio.create_task(editor.read_line())
        await asyncio.sleep(0)
        os.write(master, "\x1b[200~第一行\n第二行\x1b[201~".encode("utf-8"))
        await asyncio.sleep(0.05)
        assert not task.done()
        os.write(master, b"\r")
        assert await asyncio.wait_for(task, timeout=1) == "第一行\n第二行"
        assert "200~" not in writer.text and "201~" not in writer.text
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_backspace_removes_last_visible_char() -> None:
    master, slave = _open_pty()
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        assert await _read(editor, master, b"abc\x7f\r") == "ab"
        assert "\b \b" in writer.text
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_ctrl_d_flushes_or_signals_eof() -> None:
    master, slave = _open_pty()
    editor = TtyLineEditor(_FdStream(slave), _CaptureWriter())
    try:
        assert await _read(editor, master, b"\x04") is None
        assert await _read(editor, master, b"abc\x04") == "abc"
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_close_restores_termios_and_disables_protocols() -> None:
    master, slave = _open_pty()
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    before = termios.tcgetattr(slave)
    task = asyncio.create_task(editor.read_line())
    await asyncio.sleep(0)
    assert termios.tcgetattr(slave) != before
    assert ENABLE_PROTOCOLS in writer.text
    editor.close()
    assert termios.tcgetattr(slave) == before
    assert DISABLE_PROTOCOLS in writer.text
    with suppress(asyncio.CancelledError):
        task.cancel()
        await task
    os.close(master)
    os.close(slave)


@pytest.mark.asyncio
async def test_pause_restores_terminal_and_resume_reenters_raw_mode() -> None:
    master, slave = _open_pty()
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    before = termios.tcgetattr(slave)
    task = asyncio.create_task(editor.read_line())
    await asyncio.sleep(0)
    assert termios.tcgetattr(slave) != before
    await editor.pause()
    assert termios.tcgetattr(slave) == before
    assert DISABLE_PROTOCOLS in writer.text
    await editor.resume()
    assert termios.tcgetattr(slave) != before
    assert writer.text.count(ENABLE_PROTOCOLS) >= 2
    os.write(master, "恢复\n".encode("utf-8").replace(b"\n", b"\r"))
    assert await asyncio.wait_for(task, timeout=1) == "恢复"
    editor.close()
    os.close(master)
    os.close(slave)
