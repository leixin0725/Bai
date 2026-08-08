"""[2026-08-08] 最小后台执行器：提交、串行执行、状态记录；无优先级/取消/持久化。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from bai_agent.domain.models import BackgroundTaskRecord, TaskStatus, new_id
from bai_agent.domain.ports import SystemClock


class BackgroundExecutor:
    """[2026-08-08] 同一进程内按提交顺序串行执行；失败保留原因且不中断后续任务。"""

    def __init__(self, *, clock: Any = None) -> None:
        self._clock = clock or SystemClock()
        self._queue: asyncio.Queue[
            tuple[str, Callable[[], Awaitable[Any]]] | None
        ] = asyncio.Queue()
        self._records: dict[str, BackgroundTaskRecord] = {}

    def submit(self, name: str, coro_factory: Callable[[], Awaitable[Any]]) -> str:
        """[2026-08-08] coro_factory 延迟创建协程，避免未执行协程被丢弃告警。"""
        task_id = new_id("task")
        self._records[task_id] = BackgroundTaskRecord(
            task_id=task_id,
            name=name,
            status=TaskStatus.WAITING,
            created_at=self._clock.now(),
        )
        self._queue.put_nowait((task_id, coro_factory))
        return task_id

    @property
    def records(self) -> tuple[BackgroundTaskRecord, ...]:
        return tuple(self._records.values())

    def record(self, task_id: str) -> BackgroundTaskRecord | None:
        return self._records.get(task_id)

    async def run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            task_id, factory = item
            self._records[task_id] = self._records[task_id].model_copy(
                update={
                    "status": TaskStatus.RUNNING,
                    "started_at": self._clock.now(),
                }
            )
            try:
                await factory()
            except Exception as exc:  # noqa: BLE001  [2026-08-08] 任务失败只记录原因
                self._records[task_id] = self._records[task_id].model_copy(
                    update={
                        "status": TaskStatus.FAILURE,
                        "finished_at": self._clock.now(),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            else:
                self._records[task_id] = self._records[task_id].model_copy(
                    update={
                        "status": TaskStatus.SUCCESS,
                        "finished_at": self._clock.now(),
                    }
                )

    async def stop(self) -> None:
        await self._queue.put(None)
