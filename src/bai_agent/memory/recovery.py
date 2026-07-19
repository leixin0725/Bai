"""[2026-07-19] 原子替换与单写者锁是所有文件存储共享的恢复基础。"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import tempfile

from filelock import FileLock, Timeout

from bai_agent.domain.errors import BaiError


FailureHook = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _sync_directory(path: Path) -> None:
    """[2026-07-19] Windows 不支持通用目录 fsync；POSIX 支持时同步目录项。"""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(target: Path, payload: bytes, failure_hook: FailureHook | None = None) -> None:
    hook = failure_hook or _noop
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp-atomic", dir=target.parent
    )
    hook("temp_created")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            hook("written")
            stream.flush()
            hook("flushed")
            os.fsync(stream.fileno())
            hook("fsynced")
        hook("before_replace")
        os.replace(temporary_name, target)
        _sync_directory(target.parent)
        hook("after_replace")
    finally:
        # [2026-07-19] 故障残留可用于审计；测试前置故障不自动当成正式状态。
        if os.path.exists(temporary_name) and failure_hook is None:
            os.unlink(temporary_name)


def find_temporary_files(directory: Path) -> tuple[Path, ...]:
    return tuple(sorted(directory.glob("*.tmp-atomic"))) if directory.exists() else ()


class WriterLease:
    def __init__(self, path: Path, timeout: float = 0) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(path), timeout=timeout)
        self.acquired = False

    def acquire(self) -> None:
        try:
            self._lock.acquire()
        except Timeout as exc:
            raise BaiError("WRITER_LOCKED", "另一实例已持有记忆写锁。", retryable=True) from exc
        self.acquired = True

    def release(self) -> None:
        if self.acquired:
            self._lock.release()
            self.acquired = False

    def __enter__(self) -> "WriterLease":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

