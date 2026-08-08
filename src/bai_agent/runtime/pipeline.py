"""[2026-08-08] 串行消息处理管道：对话/定时/系统输入统一进入 FIFO，单 worker 防重入。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import PipelineItem, PipelineItemKind, new_id
from bai_agent.domain.ports import SystemClock


class ProcessingPipeline:
    """[2026-08-08] 单 worker 消费 FIFO；同一时刻至多一个处理项运行。"""

    def __init__(
        self,
        handle_item: Callable[[PipelineItem], Awaitable[None]],
        *,
        clock: Any = None,
    ) -> None:
        self._handle_item = handle_item
        self._clock = clock or SystemClock()
        self._queue: asyncio.Queue[PipelineItem | None] = asyncio.Queue()
        self._sequence = 0
        self._current_item_id: str | None = None
        self._stopping = False

    async def submit(self, kind: PipelineItemKind, payload: dict[str, Any]) -> PipelineItem:
        """[2026-08-08] 停止后不再接收新工作；sequence 由管道单调分配。"""
        if self._stopping:
            raise BaiError("RUNTIME_STOPPING", "运行时正在停止，不再接收新的处理项。")
        self._sequence += 1
        item = PipelineItem(
            item_id=new_id("item"),
            kind=kind,
            payload=payload,
            submitted_at=self._clock.now(),
            sequence=self._sequence,
        )
        await self._queue.put(item)
        return item

    @property
    def current_item_id(self) -> str | None:
        return self._current_item_id

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def stopping(self) -> bool:
        return self._stopping

    async def run(self) -> None:
        """[2026-08-08] worker 循环；处理项异常向上传播（chat 失败语义由外壳/CLI 决定）。"""
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                self._current_item_id = item.item_id
                try:
                    await self._handle_item(item)
                finally:
                    self._current_item_id = None
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        """[2026-08-08] 置 stopping 并丢弃未开始的排队项，当前处理项结束后退出。"""
        self._stopping = True
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._queue.task_done()
        await self._queue.put(None)
