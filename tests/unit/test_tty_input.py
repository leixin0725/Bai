"""[2026-08-10] TTY 行编辑器：Enter 发送、Shift+Enter/Ctrl+J 换行、粘贴不回显标记。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import fcntl
import struct

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
    _last_grapheme_span,
    _line_layout,
    display_width,
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


def _set_pty_size(fd: int, rows: int, columns: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abc", 3),
        ("中文", 4),
        ("a中b", 4),
        ("中文abc", 7),
        ("👍", 2),
        ("e\u0301", 1),
        ("中\u0301", 2),
        ("👨\u200d👩\u200d👧\u200d👦", 2),
    ],
)
def test_display_width_uses_terminal_cells(text: str, expected: int) -> None:
    assert display_width(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("中文", (1, 2, 2)),
        ("a中b", (2, 3, 1)),
        ("中文abc", (4, 5, 1)),
        ("e\u0301", (0, 2, 1)),
        ("中\u0301", (0, 2, 2)),
        ("👍", (0, 1, 2)),
        ("👨\u200d👩\u200d👧\u200d👦", (0, 7, 2)),
        ("🇨🇳", (0, 2, 2)),
        ("🇨🇳x", (2, 3, 1)),
        ("🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f", (0, 7, 2)),
        ("\u0301", (0, 1, 0)),
        ("a\t", (1, 2, 1)),
    ],
)
def test_last_grapheme_span_groups_visible_clusters(
    text: str, expected: tuple[int, int, int]
) -> None:
    assert _last_grapheme_span(text) == expected


@pytest.mark.parametrize(
    ("text", "width", "expected"),
    [
        ("", 5, (1, 0, 0)),
        ("abcde", 5, (1, 0, 5)),
        ("abcdef", 5, (2, 1, 1)),
        ("中文", 3, (2, 1, 2)),
        ("ab中", 3, (2, 1, 2)),
        ("abcde中", 5, (2, 1, 2)),
    ],
)
def test_line_layout_simulates_terminal_wrap(
    text: str, width: int, expected: tuple[int, int, int]
) -> None:
    assert _line_layout(text, width) == expected


@pytest.mark.asyncio
async def test_backspace_erases_cjk_by_terminal_width() -> None:
    master, slave = _open_pty()
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        assert await _read(editor, master, "中文".encode("utf-8") + b"\x7f\r") == "中"
        assert "\b\b  \b\b" in writer.text
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected", "erase_sequence"),
    [
        ("abc", "ab", "\b \b"),
        ("中文", "中", "\b\b  \b\b"),
        ("a中b", "a", "\b \b\b\b  \b\b"),
        ("中文abc", "中文", "\b \b\b \b\b \b"),
    ],
)
async def test_backspace_sequence_matches_mixed_width_deletions(
    text: str, expected: str, erase_sequence: str
) -> None:
    master, slave = _open_pty()
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        payload = text.encode("utf-8") + b"\x7f" * (len(text) - len(expected)) + b"\r"
        assert await _read(editor, master, payload) == expected
        assert erase_sequence in writer.text
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_continue_typing_after_mixed_backspace_keeps_display_consistent() -> None:
    master, slave = _open_pty()
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        assert (
            await _read(editor, master, "a中b".encode("utf-8") + b"\x7f\x7fc\r")
            == "ac"
        )
        assert writer.text.endswith(
            "a中b" + "\b \b" + "\b\b  \b\b" + "c" + "\r\n"
        )
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_backspace_on_unwrapped_line_keeps_simple_erase() -> None:
    master, slave = _open_pty()
    _set_pty_size(slave, 24, 5)
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        assert await _read(editor, master, b"abcde\x7f\r") == "abcd"
        assert "\b \b" in writer.text
        assert "\x1b[1A" not in writer.text
        assert "\x1b[K" not in writer.text
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected", "redrawn_line"),
    [
        ("abcdef", "abcde", "abcde"),
        ("中中", "中", "中"),
        ("ab中", "ab", "ab"),
    ],
)
async def test_backspace_across_wrap_redraws_current_line(
    text: str, expected: str, redrawn_line: str
) -> None:
    master, slave = _open_pty()
    _set_pty_size(slave, 24, 5 if "中" not in text else 3)
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        assert (
            await _read(editor, master, text.encode("utf-8") + b"\x7f\r")
            == expected
        )
        assert (
            "\x1b[1A\r\x1b[K\x1b[1B\x1b[K\x1b[1A\r" + redrawn_line
            in writer.text
        )
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_continue_typing_after_wrapped_backspace_keeps_display_consistent() -> None:
    master, slave = _open_pty()
    _set_pty_size(slave, 24, 5)
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        assert (
            await _read(editor, master, b"abcdef\x7fg\r")
            == "abcdeg"
        )
        assert (
            "\x1b[1A\r\x1b[K\x1b[1B\x1b[K\x1b[1A\rabcdeg"
            in writer.text
        )
    finally:
        editor.close()
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "erase_sequence"),
    [
        ("👍".encode("utf-8"), "\b\b  \b\b"),
        ("👨\u200d👩\u200d👧\u200d👦".encode("utf-8"), "\b\b  \b\b"),
        ("🇨🇳".encode("utf-8"), "\b\b  \b\b"),
        ("e\u0301".encode("utf-8"), "\b \b"),
        ("中\u0301".encode("utf-8"), "\b\b  \b\b"),
    ],
)
async def test_backspace_removes_whole_emoji_and_combining_grapheme(
    payload: bytes, erase_sequence: str
) -> None:
    master, slave = _open_pty()
    writer = _CaptureWriter()
    editor = TtyLineEditor(_FdStream(slave), writer)
    try:
        assert await _read(editor, master, payload + b"\x7f\r") == ""
        assert erase_sequence in writer.text
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
