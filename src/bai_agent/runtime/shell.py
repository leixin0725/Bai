"""[2026-08-08] 运行时外壳：串行管道、生命周期、状态快照与配置重载可见性。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
import json
from time import monotonic
from typing import Any

from bai_agent.domain.errors import BaiError, TurnInterrupted, TurnRejected
from bai_agent.domain.models import (
    ConversationAction,
    HealthState,
    InputBoundary,
    PipelineItem,
    PipelineItemKind,
    ReloadStatus,
    RuntimeStatus,
    SessionState,
    TaskStatus,
)
from bai_agent.domain.ports import SystemClock
from bai_agent.runtime.executor import BackgroundExecutor
from bai_agent.runtime.input_reader import InputReader
from bai_agent.runtime.pipeline import ProcessingPipeline


class RuntimeShell:
    """[2026-08-08] 单用户会话外壳：chat 输入经管道串行处理，事件经注册处理函数投递。"""

    def __init__(
        self,
        application: Any,
        *,
        on_output: Callable[[str], None] | None = None,
        on_warning: Callable[[str], None] | None = None,
        clock: Any = None,
    ) -> None:
        self.application = application
        self._on_output = on_output or (lambda text: None)
        self._on_warning = on_warning or (lambda text: None)
        self._clock = clock or SystemClock()
        self._pipeline = ProcessingPipeline(self._handle_item, clock=self._clock)
        self._executor = BackgroundExecutor(clock=self._clock)
        self._session_state = SessionState.IDLE
        self._started_at = monotonic()
        self._last_reload = ReloadStatus(revision="", ok=True, error=None)
        self._counters: dict[str, int] = {
            "chat_turns": 0,
            "events": 0,
            "events_failed": 0,
            "tasks_succeeded": 0,
            "tasks_failed": 0,
        }
        self._stop_event = asyncio.Event()
        self._stop_reason = "eof"
        self._worker_task: asyncio.Task[None] | None = None
        self._worker_error: BaseException | None = None
        self._event_handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}

    @property
    def session_state(self) -> SessionState:
        return self._session_state

    @property
    def pipeline(self) -> ProcessingPipeline:
        return self._pipeline

    @property
    def executor(self) -> BackgroundExecutor:
        return self._executor

    def request_stop(self, reason: str) -> None:
        """[2026-08-08] 第一次停止信号：不再接收新工作，当前处理项结束后优雅退出。"""
        self._stop_reason = reason
        self._stop_event.set()

    def request_abort(self) -> None:
        """[2026-08-08] 第二次停止信号：取消当前处理项（保持 Ctrl+C 立即中止语义）。"""
        self._stop_reason = "sigint"
        self._stop_event.set()
        if self._worker_task is not None:
            self._worker_task.cancel()

    def register_event_handler(
        self,
        event_kind: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._event_handlers[event_kind] = handler

    def submit_task(
        self,
        name: str,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> str:
        return self._executor.submit(name, coro_factory)

    async def submit_chat(
        self,
        text: str,
        *,
        resume_pending: bool = False,
        turn_id: str | None = None,
        source_boundary: InputBoundary = InputBoundary.BUFFER_EMPTY,
    ) -> None:
        payload: dict[str, Any] = {
            "text": text,
            "source_boundary": source_boundary.value,
            "resume_pending": resume_pending,
            "turn_id": turn_id,
        }
        await self._pipeline.submit(PipelineItemKind.CHAT_INPUT, payload)

    def status_snapshot(self) -> RuntimeStatus:
        """[2026-08-08] 一致快照：非处理中状态不携带当前处理项，避免状态间隙。"""
        pending = getattr(self.application, "archive", None)
        pending_id = None
        if pending is not None:
            record = pending.pending_turn()
            pending_id = getattr(record, "turn_id", None)
        current = (
            self._pipeline.current_item_id
            if self._session_state is SessionState.PROCESSING
            else None
        )
        tasks = self._executor.records
        failed_tasks = any(item.status is TaskStatus.FAILURE for item in tasks)
        health = (
            HealthState.WARNING
            if (not self._last_reload.ok or failed_tasks)
            else HealthState.OK
        )
        counters = dict(self._counters)
        counters["tasks_succeeded"] = sum(
            1 for item in tasks if item.status is TaskStatus.SUCCESS
        )
        counters["tasks_failed"] = sum(
            1 for item in tasks if item.status is TaskStatus.FAILURE
        )
        return RuntimeStatus(
            session_state=self._session_state,
            queue_depth=self._pipeline.queue_depth,
            current_item_id=current,
            tasks=tasks,
            health=health,
            last_reload=self._last_reload,
            pending_turn_id=pending_id,
            counters=counters,
            uptime_seconds=monotonic() - self._started_at,
        )

    async def run(
        self,
        source: Any,
        *,
        resume: tuple[str, str] | None = None,
    ) -> int:
        """[2026-08-08] 运行外壳直到 EOF 或停止信号；返回退出码（130=中断，0=正常）。"""
        self._worker_task = asyncio.create_task(self._pipeline.run())
        executor_task = asyncio.create_task(self._executor.run())
        watcher = asyncio.create_task(self._watch_worker())
        if resume is not None:
            content, turn_id = resume
            await self.submit_chat(content, resume_pending=True, turn_id=turn_id)
        reader = asyncio.create_task(self._read_source(source))
        try:
            await self._stop_event.wait()
        finally:
            self._session_state = SessionState.STOPPING
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
            await self._pipeline.stop()
            await self._executor.stop()
            await executor_task
            await watcher
        if self._worker_error is not None:
            raise self._worker_error
        return 130 if self._stop_reason == "sigint" else 0

    async def _watch_worker(self) -> None:
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        except BaseException as exc:  # noqa: BLE001  [2026-08-08] 错误在清理后统一向上传播
            self._worker_error = exc
        finally:
            self._stop_event.set()

    async def _read_source(self, source: Any) -> None:
        """[2026-08-08] 输入读取器按一次输入动作合并后提交；EOF 触发优雅停止。"""
        reader = InputReader(
            source,
            on_action=self._on_input_action,
            on_eof=self._on_input_eof,
        )
        await reader.run()

    async def _on_input_action(self, action: ConversationAction) -> None:
        await self.submit_chat(
            action.text,
            source_boundary=action.source_boundary,
        )

    def _on_input_eof(self) -> None:
        if not self._stop_event.is_set():
            self.request_stop("eof")

    async def _handle_item(self, item: PipelineItem) -> None:
        if item.kind is PipelineItemKind.CHAT_INPUT:
            await self._handle_chat(item)
            return
        await self._handle_event(item)

    async def _handle_chat(self, item: PipelineItem) -> None:
        self._session_state = SessionState.PROCESSING
        text = str(item.payload["text"])
        try:
            if text == ":status":
                self._emit_status()
                return
            await self._run_chat_text(text, item.payload)
            self._counters["chat_turns"] += 1
        except TurnRejected as exc:
            if isinstance(exc, TurnInterrupted):
                self.request_stop("sigint")
                raise
        finally:
            self._session_state = (
                SessionState.STOPPING if self._stop_event.is_set() else SessionState.IDLE
            )

    async def _run_chat_text(self, text: str, payload: dict[str, Any]) -> None:
        kwargs: dict[str, Any] = {}
        if payload.get("resume_pending"):
            kwargs["resume_pending"] = True
            if payload.get("turn_id"):
                kwargs["turn_id"] = str(payload["turn_id"])
        if hasattr(self.application, "reload_config_with_status"):
            self._reload_with_visibility()
            await self.application.run_turn(text, reload_config=False, **kwargs)
            return
        await self.application.run_turn(text, **kwargs)

    def _reload_with_visibility(self) -> None:
        """[2026-08-08] 重载失败先输出明确警告再继续旧快照；禁止静默回退。"""
        status = self.application.reload_config_with_status()
        self._last_reload = status
        if not status.ok:
            detail = status.error or {}
            warning = (
                f"[警告] 配置重载失败，继续使用 config_revision={status.revision}\n"
                f"  分组: {detail.get('group', '?')}\n"
                f"  字段: {detail.get('field', '?')}\n"
                f"  原因: {detail.get('reason', '?')}"
            )
            self._on_warning(warning)

    async def _handle_event(self, item: PipelineItem) -> None:
        self._counters["events"] += 1
        handler = self._event_handlers.get(str(item.payload.get("event_kind", "")))
        if handler is None:
            return
        try:
            await handler(item.payload)
        except Exception:  # noqa: BLE001  [2026-08-08] 事件失败只计数，不中断后续处理项
            self._counters["events_failed"] += 1

    def _emit_status(self) -> None:
        payload = self.status_snapshot().model_dump(mode="json")
        payload["ok"] = True
        self._on_output(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
