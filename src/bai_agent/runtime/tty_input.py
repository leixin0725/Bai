"""[2026-08-10] TTY 行编辑器：raw 模式逐键读取，Enter 发送、Shift+Enter 换行、粘贴不显示标记。"""

from __future__ import annotations

import asyncio
import os
import termios
import tty
import unicodedata
from typing import Any

from rich.cells import cell_len, split_graphemes


ENABLE_PROTOCOLS = "\x1b[?2004h\x1b[>1u"
DISABLE_PROTOCOLS = "\x1b[?2004l\x1b[<1u"
BRACKETED_PASTE_START = "\x1b[200~"
BRACKETED_PASTE_END = "\x1b[201~"
KITTY_SHIFT_ENTER = "\x1b[13;2u"


def display_width(text: str) -> int:
    """[2026-08-10] 文本在终端中占用的 cell 宽度，委托 Rich 的 Unicode 宽度表。"""
    return cell_len(text)


def _last_grapheme_span(text: str) -> tuple[int, int, int] | None:
    """[2026-08-10] 返回一行文本最后一个 grapheme 的 [start, end) 与 cell 宽度。

    退格按终端显示单元删除，而不是按 Python 字符数：普通 CJK 占 2 列、
    组合字符并入前一可见字符、ZWJ/VS16 emoji 序列按一个 grapheme 处理。
    制表符本编辑器不追踪 tab stop，仍按旧的 1 列近似处理，避免被并入前一字符。
    """
    if not text:
        return None
    if text[-1] == "\t":
        return len(text) - 1, len(text), 1
    # Regional indicator 成对构成旗帜 emoji；Rich 的简化分组把它们当两个 1 列字符，
    # 这里按终端显示习惯把最后一对当作一个 2 列 grapheme。
    if (
        len(text) >= 2
        and "\U0001f1e6" <= text[-1] <= "\U0001f1ff"
        and "\U0001f1e6" <= text[-2] <= "\U0001f1ff"
    ):
        return len(text) - 2, len(text), 2
    spans, _ = split_graphemes(text)
    if not spans:
        return None
    start, end, _ = spans[-1]
    return start, end, display_width(text[start:end])


def _line_layout(text: str, terminal_width: int) -> tuple[int, int, int]:
    """[2026-08-10] 模拟终端自动换行，返回 (物理行数, 光标所在行, 光标列)。

    宽字符在仅剩 1 列时会被终端放到下一行行首，避免拆字；该规则在此同步模拟。
    """
    if terminal_width <= 0:
        return 1, 0, display_width(text)
    rows = 0
    column = 0
    spans, _ = split_graphemes(text)
    for start, end, _ in spans:
        chunk = text[start:end]
        # 与 _last_grapheme_span 保持一致：本编辑器不追踪 tab stop，按 1 列近似。
        width = 1 if chunk.endswith("\t") else display_width(chunk)
        if width <= 0:
            continue
        if column + width <= terminal_width:
            column += width
        else:
            rows += 1
            if width > 1 and column == terminal_width - 1:
                column = width
            else:
                column = width
    return rows + 1, rows, column


class TtyLineEditor:
    """[2026-08-10] 最小 raw 模式编辑器：UTF-8 输入、退格、Enter/Shift+Enter、括号粘贴与 Ctrl+D。

    不提供方向键/历史等复杂编辑（本阶段范围外）；ISIG 保留，Ctrl+C 走既有信号优雅停止。
    回显由本编辑器控制，因此括号粘贴标记（CSI 200~ / 201~）不会出现在终端。
    退格按终端 cell 宽度擦除；遇到自动换行的续行时整行重绘，避免光标无法回到上一行。
    Shift+Enter/Ctrl+J 换出的空行同样可用退格删掉换行符并回上一行。
    """

    def __init__(self, stdin: Any, stdout: Any) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._fd = stdin.fileno()
        self._saved_attrs: list[Any] | None = None
        self._raw_mode = False
        self._paused = False
        self._closed = False
        self._buffer: list[str] = []
        self._pending = bytearray()
        self._paste_mode = False
        self._results: list[str | None] = []
        self._wake: asyncio.Future[bool] | None = None
        self._resume_event: asyncio.Event | None = None

    @property
    def is_tty(self) -> bool:
        return True

    async def buffered(self) -> bool:
        # [2026-08-10] 编辑器每次 read_line 只返回一个完整动作，无需缓冲合并。
        return False

    def _enter_raw(self) -> None:
        if self._raw_mode:
            return
        self._saved_attrs = termios.tcgetattr(self._fd)
        tty.setraw(self._fd, termios.TCSANOW)
        attrs = termios.tcgetattr(self._fd)
        attrs[3] |= termios.ISIG
        attrs[1] |= termios.OPOST
        termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
        self._raw_mode = True
        self._write(ENABLE_PROTOCOLS)

    def _restore(self) -> None:
        if self._raw_mode and self._saved_attrs is not None:
            self._write(DISABLE_PROTOCOLS)
            termios.tcsetattr(self._fd, termios.TCSANOW, self._saved_attrs)
            self._raw_mode = False

    def close(self) -> None:
        """[2026-08-10] 退出前恢复终端并关闭协议，避免残留 raw/2004/kitty 状态。"""
        self._closed = True
        self._restore()
        wake = self._wake
        if wake is not None and not wake.done():
            wake.set_result(True)

    async def pause(self) -> None:
        """[2026-08-10] TUI 等独占终端期间交还 stdin：恢复 canonical 并移除监听。"""
        self._paused = True
        self._restore()
        wake = self._wake
        if wake is not None and not wake.done():
            wake.set_result(True)

    async def resume(self) -> None:
        """[2026-08-10] 独占结束：重新进入 raw 模式并重发协议，未消费字节保留。"""
        self._paused = False
        self._enter_raw()
        event = self._resume_event
        if event is not None:
            event.set()

    async def read_line(self) -> str | None:
        """[2026-08-10] 等待一个完整输入：Enter 返回正文，Ctrl+D 空缓冲返回 EOF。"""
        loop = asyncio.get_running_loop()
        while True:
            if self._results:
                return self._results.pop(0)
            if self._closed:
                return None
            if self._paused:
                self._resume_event = asyncio.Event()
                try:
                    await self._resume_event.wait()
                finally:
                    self._resume_event = None
                continue
            self._enter_raw()
            future = loop.create_future()
            self._wake = future
            try:
                loop.add_reader(self._fd, lambda: self._on_readable(future))
                await future
            finally:
                self._wake = None
                loop.remove_reader(self._fd)

    def _on_readable(self, future: asyncio.Future[bool]) -> None:
        if future.done():
            return
        try:
            chunk = os.read(self._fd, 4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            chunk = b""
        if not chunk:
            self._results.append(None)
        else:
            self._feed(chunk)
        future.set_result(True)

    def _feed(self, chunk: bytes) -> None:
        self._pending.extend(chunk)
        while self._pending:
            if self._pending[0] == 0x1B:
                sequence = self._consume_escape()
                if sequence is None:
                    break
                self._handle_escape(sequence)
            else:
                char = self._consume_utf8()
                if char is None:
                    break
                self._handle_char(char)

    def _consume_utf8(self) -> str | None:
        first = self._pending[0]
        if first < 0x80:
            length = 1
        elif first < 0xC0:
            length = 1
        elif first < 0xE0:
            length = 2
        elif first < 0xF0:
            length = 3
        else:
            length = 4
        if len(self._pending) < length:
            return None
        raw = bytes(self._pending[:length])
        del self._pending[:length]
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")

    def _consume_escape(self) -> str | None:
        if len(self._pending) < 2:
            return None
        if self._pending[1] != 0x5B:  # 非 CSI（ESC+单字符），直接消费两个字节
            raw = bytes(self._pending[:2])
            del self._pending[:2]
            return raw.decode("latin-1")
        index = 2
        while index < len(self._pending):
            byte = self._pending[index]
            if 0x40 <= byte <= 0x7E:
                raw = bytes(self._pending[: index + 1])
                del self._pending[: index + 1]
                return raw.decode("latin-1")
            index += 1
        if len(self._pending) > 64:
            raw = bytes(self._pending[:2])
            del self._pending[:2]
            return raw.decode("latin-1")
        return None

    def _handle_escape(self, sequence: str) -> None:
        if sequence == BRACKETED_PASTE_START:
            self._paste_mode = True
            return
        if sequence == BRACKETED_PASTE_END:
            self._paste_mode = False
            return
        if sequence == KITTY_SHIFT_ENTER and not self._paste_mode:
            self._insert_newline()
            return
        # 其余 CSI（方向键等）本阶段忽略，不回显。

    def _handle_char(self, char: str) -> None:
        if self._paste_mode:
            if char in ("\r", "\n"):
                self._insert_newline()
            else:
                self._buffer.append(char)
                self._echo(char)
            return
        if char == "\r":
            self._submit()
            return
        if char == "\n":
            self._insert_newline()
            return
        if char in ("\x7f", "\x08"):
            self._backspace()
            return
        if char == "\x04":
            if self._buffer:
                self._submit()
            else:
                self._results.append(None)
            return
        # Cf 是零宽格式字符（ZWJ、emoji tag、bidi 控制等）；保留它们才能让
        # ZWJ emoji / tag flag 等 grapheme 在后续退格时作为一个整体被删除。
        if char.isprintable() or char in ("\t", " ") or unicodedata.category(char) == "Cf":
            self._buffer.append(char)
            self._echo(char)

    def _insert_newline(self) -> None:
        self._buffer.append("\n")
        self._echo("\r\n")

    def _backspace(self) -> None:
        if not self._buffer:
            return
        if self._buffer[-1] == "\n":
            self._delete_newline()
            return
        # 只回看当前行，避免把前一行换行符并入 grapheme 计算。
        line_start = len(self._buffer)
        while line_start > 0 and self._buffer[line_start - 1] != "\n":
            line_start -= 1
        span = _last_grapheme_span("".join(self._buffer[line_start:]))
        if span is None:
            return
        start, end, width = span
        _, old_row, _ = _line_layout(
            "".join(self._buffer[line_start:]), self._terminal_width()
        )
        del self._buffer[line_start + start : line_start + end]
        new_line = "".join(self._buffer[line_start:])
        if old_row > 0:
            self._redraw_current_line(old_row, new_line)
        elif width > 0:
            self._echo("\b" * width + " " * width + "\b" * width)

    def _delete_newline(self) -> None:
        """[2026-08-10] 删除行尾换行符：把当前空行并回上一行并整行重绘。"""
        newline_index = len(self._buffer) - 1
        prev_start = newline_index
        while prev_start > 0 and self._buffer[prev_start - 1] != "\n":
            prev_start -= 1
        prev_line = "".join(self._buffer[prev_start:newline_index])
        prev_rows, _, _ = _line_layout(prev_line, self._terminal_width())
        del self._buffer[newline_index]
        # 删除换行后，从上一行行首到当前行行尾的文本构成新的当前行；
        # 光标正位于当前空行行首，上移 prev_rows 行即回到合并后的行首。
        combined = "".join(self._buffer[prev_start:])
        self._redraw_current_line(prev_rows, combined)

    def _submit(self) -> None:
        self._results.append("".join(self._buffer))
        self._buffer = []
        self._echo("\r\n")

    def _echo(self, text: str) -> None:
        try:
            self._stdout.write(text)
            self._stdout.flush()
        except (AttributeError, OSError, ValueError):
            return

    def _write(self, text: str) -> None:
        try:
            self._stdout.write(text)
            self._stdout.flush()
        except (AttributeError, OSError, ValueError):
            return

    def _terminal_width(self) -> int:
        try:
            columns = os.get_terminal_size(self._fd).columns
        except (OSError, ValueError, AttributeError):
            return 80
        return columns if columns > 0 else 80

    def _redraw_current_line(self, old_row: int, new_line: str) -> None:
        """[2026-08-10] 回到逻辑行首，清到屏尾后重绘当前行。

        逐行 EL 在宽字符/行尾场景可能清不干净（第一行末尾残留），
        因此从行首一次性 ED（\x1b[J）清掉旧行及下方空白，再重绘。
        """
        if old_row > 0:
            self._echo(f"\x1b[{old_row}A")
        self._echo("\r\x1b[J")
        self._echo(new_line)
