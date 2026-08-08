"""[2026-08-08] stdin 输入适配器；一次输入动作的合并逻辑在 InputReader 中实现。"""

from __future__ import annotations

import asyncio
import os
import select
import sys
from typing import Any


class StdinInputSource:
    """[2026-08-08] 真实文件描述符走 add_reader 分块读取（不阻塞事件循环、无线程）；内存流直接同步读。"""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._fd: int | None = None
        self._buffer = bytearray()
        self._eof = False
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

    async def buffered(self) -> bool:
        """[2026-08-08] 零等待判定：stdin 已有更多数据则合并；Windows 无等效路径时降级为逐行。"""
        if sys.platform == "win32" or self._fd is None:
            return False
        try:
            readable, _, _ = select.select([self._fd], [], [], 0)
            return bool(readable)
        except (OSError, ValueError):
            return False

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
                if not self._buffer:
                    return None
                raw = bytes(self._buffer)
                self._buffer.clear()
                return raw.decode("utf-8", errors="replace")
            future = loop.create_future()

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
                    self._buffer.extend(chunk)
                if not future.done():
                    future.set_result(True)

            try:
                loop.add_reader(self._fd, on_readable)
            except NotImplementedError:
                # [2026-08-08] Windows 次要兼容：阻塞式逐行读取，不做合并。
                line = self._stream.readline()
                return None if line == "" else line
            try:
                await future
            finally:
                loop.remove_reader(self._fd)
