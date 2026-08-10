"""[2026-08-08] stdin 输入适配器；一次输入动作的合并逻辑在 InputReader 中实现。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import os
import select
from typing import Any

from bai_agent.domain.models import ConversationAction, InputBoundary, new_id


# [2026-08-10] 括号粘贴标记：终端把一次粘贴包裹在 CSI 200~ … 201~ 之间，
# 作为确定性粘贴边界，不依赖送达时序与时间窗口。
BRACKETED_PASTE_START = b"\x1b[200~"
BRACKETED_PASTE_END = b"\x1b[201~"


def enable_bracketed_paste(stream: Any) -> None:
    """[2026-08-10] 仅 TTY 启用括号粘贴；非 TTY 静默跳过。"""
    try:
        if stream is None or not stream.isatty():
            return
        stream.write("\x1b[?2004h")
        stream.flush()
    except (AttributeError, OSError, ValueError):
        return


def disable_bracketed_paste(stream: Any) -> None:
    """[2026-08-10] 退出前关闭括号粘贴，避免终端残留 2004 状态。"""
    try:
        if stream is None or not stream.isatty():
            return
        stream.write("\x1b[?2004l")
        stream.flush()
    except (AttributeError, OSError, ValueError):
        return


class StdinInputSource:
    """[2026-08-08] 真实文件描述符走 add_reader 分块读取（不阻塞事件循环、无线程）；内存流直接同步读。"""

    def __init__(self, stream: Any, *, bracketed_stdout: Any = None) -> None:
        self._stream = stream
        self._bracketed_stdout = bracketed_stdout
        self._fd: int | None = None
        self._buffer = bytearray()
        self._eof = False
        self._paused = False
        self._read_pending: asyncio.Future[bool] | None = None
        self._resume_event: asyncio.Event | None = None
        self._in_bracketed_paste = False
        try:
            fd = stream.fileno()
            self._fd = fd if isinstance(fd, int) and fd >= 0 else None
        except (AttributeError, OSError, ValueError):
            self._fd = None

    @property
    def is_tty(self) -> bool:
        try:
            return bool(self._stream.isatty())
        except (AttributeError, OSError):
            return False

    async def read_line(self) -> str | None:
        if self._fd is None:
            line = self._stream.readline()
            return None if line == "" else line
        return await self._read_fd_line()

    def close(self) -> None:
        """[2026-08-10] 统一关闭接口：该路径不持有终端状态，无操作。"""
        return None

    async def buffered(self) -> bool:
        """[2026-08-08] 零等待判定：stdin 已有更多数据则合并。"""
        # [2026-08-10] 已读入适配器缓冲但尚未消费的行仍属于"连片输入"，
        # 否则一次粘贴被 os.read 整块读入后，fd 无可读字节会误判为逐行提交。
        if self._buffer:
            return True
        # [2026-08-10] 括号粘贴进行中即使 fd 暂时无数据也必须继续累积，
        # 否则终端逐行送达粘贴内容时会在第一个回车处误判为发送。
        if self._in_bracketed_paste:
            return True
        if self._fd is None:
            return False
        try:
            readable, _, _ = select.select([self._fd], [], [], 0)
            return bool(readable)
        except (OSError, ValueError):
            return False

    async def pause(self) -> None:
        """[2026-08-08] TUI 等独占终端期间暂停读取，把 stdin 让给 Textual 驱动。"""
        self._paused = True
        future = self._read_pending
        if future is not None and not future.done():
            future.set_result(False)

    async def resume(self) -> None:
        """[2026-08-08] 结束独占后恢复读取；已缓冲字节保留，不会误报 EOF。"""
        self._paused = False
        event = self._resume_event
        if event is not None:
            event.set()
        # [2026-08-10] 调试 TUI 退出时会恢复自己的终端状态，这里重新启用括号粘贴。
        enable_bracketed_paste(self._bracketed_stdout)

    @property
    def in_bracketed_paste(self) -> bool:
        return self._in_bracketed_paste

    def _append_chunk(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        self._strip_markers()

    def _strip_markers(self) -> None:
        """[2026-08-10] 单遍按序剥离完整粘贴标记并维护状态；跨分块的部分标记保留到下次补齐。"""
        out = bytearray()
        index = 0
        length = len(self._buffer)
        while index < length:
            if self._buffer.startswith(BRACKETED_PASTE_START, index):
                self._in_bracketed_paste = True
                index += len(BRACKETED_PASTE_START)
                continue
            if self._buffer.startswith(BRACKETED_PASTE_END, index):
                self._in_bracketed_paste = False
                index += len(BRACKETED_PASTE_END)
                continue
            out.append(self._buffer[index])
            index += 1
        self._buffer = out

    async def _read_fd_line(self) -> str | None:
        """[2026-08-08] add_reader 驱动分块读取；newline 完整才返回行，EOF 返回剩余缓冲。"""
        loop = asyncio.get_running_loop()
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self._buffer[: newline + 1])
                del self._buffer[: newline + 1]
                return raw.decode("utf-8", errors="replace")
            if self._eof:
                self._strip_markers()
                if not self._buffer:
                    return None
                raw = bytes(self._buffer)
                self._buffer.clear()
                return raw.decode("utf-8", errors="replace")
            if self._paused:
                self._resume_event = asyncio.Event()
                try:
                    await self._resume_event.wait()
                finally:
                    self._resume_event = None
                continue
            future = loop.create_future()
            self._read_pending = future

            def on_readable() -> None:
                if future.done():
                    return
                try:
                    chunk = os.read(self._fd, 4096)
                except (BlockingIOError, InterruptedError):
                    return
                except OSError as exc:
                    if not future.done():
                        future.set_exception(exc)
                    return
                if not chunk:
                    self._eof = True
                else:
                    self._append_chunk(chunk)
                if not future.done():
                    future.set_result(True)

            try:
                loop.add_reader(self._fd, on_readable)
            except NotImplementedError:
                # [2026-08-10] 事件循环不支持 add_reader 时降级为阻塞式逐行读取；
                # Ubuntu/WSL 正常路径不会走到。
                line = self._stream.readline()
                return None if line == "" else line
            try:
                await future
            finally:
                self._read_pending = None
                loop.remove_reader(self._fd)


class InputReader:
    """[2026-08-08] 一次输入动作：管道以 EOF 为整批边界；TTY 按缓冲区连片合并，零等待无时间阈值。"""

    def __init__(
        self,
        source: Any,
        *,
        on_action: Callable[[ConversationAction], Awaitable[None]],
        on_eof: Callable[[], None] | None = None,
    ) -> None:
        self._source = source
        self._on_action = on_action
        self._on_eof = on_eof

    async def run(self) -> None:
        try:
            lines: list[str] = []
            while True:
                line = await self._source.read_line()
                if line is None:
                    if lines and "".join(lines).strip():
                        boundary = (
                            InputBoundary.PIPE_EOF
                            if not self._source.is_tty
                            else InputBoundary.BUFFER_EMPTY
                        )
                        await self._emit(lines, boundary)
                    return
                lines.append(line.rstrip("\r\n"))
                if self._source.is_tty and not await self._source.buffered():
                    if "".join(lines).strip():
                        await self._emit(lines, InputBoundary.BUFFER_EMPTY)
                    lines = []
        finally:
            if self._on_eof is not None:
                self._on_eof()

    async def _emit(self, lines: list[str], boundary: InputBoundary) -> None:
        action = ConversationAction(
            action_id=new_id("action"),
            lines=tuple(lines),
            source_boundary=boundary,
        )
        await self._on_action(action)
