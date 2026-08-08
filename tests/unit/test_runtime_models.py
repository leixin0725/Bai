"""[2026-08-08] 阶段 1 运行时 DTO 的校验与状态转换不变式。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from bai_agent.domain.models import (
    BackgroundTaskRecord,
    ConversationAction,
    HealthState,
    InputBoundary,
    PipelineItem,
    PipelineItemKind,
    ReloadStatus,
    RuntimeStatus,
    SessionState,
    TaskStatus,
    new_id,
)


UTC = timezone.utc
AT = datetime(2026, 7, 19, tzinfo=UTC)


def make_item(**overrides: object) -> PipelineItem:
    values = {
        "item_id": "item-00000000-0000-0000-0000-000000000001",
        "kind": PipelineItemKind.CHAT_INPUT,
        "payload": {"text": "你好", "source_boundary": "buffer_empty"},
        "submitted_at": AT,
        "sequence": 1,
    }
    values.update(overrides)
    return PipelineItem(**values)


def test_pipeline_item_valid_chat_input() -> None:
    item = make_item()
    assert item.kind is PipelineItemKind.CHAT_INPUT
    assert item.sequence == 1


def test_pipeline_item_rejects_missing_or_blank_chat_text() -> None:
    with pytest.raises(ValidationError):
        make_item(payload={"text": "  "})
    with pytest.raises(ValidationError):
        make_item(payload={})


def test_pipeline_item_rejects_naive_time_and_zero_sequence() -> None:
    with pytest.raises(ValidationError):
        make_item(submitted_at=datetime(2026, 7, 19))
    with pytest.raises(ValidationError):
        make_item(sequence=0)


def test_conversation_action_joins_lines_without_truncation() -> None:
    action = ConversationAction(
        action_id="action-00000000-0000-0000-0000-000000000002",
        lines=("第一行", "第二行", "第三行"),
        source_boundary=InputBoundary.PIPE_EOF,
    )
    assert action.text == "第一行\n第二行\n第三行"


def test_conversation_action_rejects_empty_or_blank_lines() -> None:
    with pytest.raises(ValidationError):
        ConversationAction(
            action_id="action-00000000-0000-0000-0000-000000000003",
            lines=(),
            source_boundary=InputBoundary.PIPE_EOF,
        )
    with pytest.raises(ValidationError):
        ConversationAction(
            action_id="action-00000000-0000-0000-0000-000000000004",
            lines=("正常", ""),
            source_boundary=InputBoundary.PIPE_EOF,
        )


def task_record(status: TaskStatus, **overrides: object) -> BackgroundTaskRecord:
    values = {
        "task_id": new_id("task"),
        "name": "curation",
        "status": status,
        "created_at": AT,
    }
    if status is TaskStatus.RUNNING:
        values["started_at"] = AT
    elif status in {TaskStatus.SUCCESS, TaskStatus.FAILURE}:
        values["started_at"] = AT
        values["finished_at"] = AT
        if status is TaskStatus.FAILURE:
            values["error"] = "受控失败"
    values.update(overrides)
    return BackgroundTaskRecord(**values)


@pytest.mark.parametrize("status", list(TaskStatus))
def test_task_status_valid_states(status: TaskStatus) -> None:
    assert task_record(status).status is status


def test_task_status_rejects_invalid_transitions() -> None:
    with pytest.raises(ValidationError):
        task_record(TaskStatus.WAITING, started_at=AT)
    with pytest.raises(ValidationError):
        task_record(TaskStatus.RUNNING, finished_at=AT)
    with pytest.raises(ValidationError):
        task_record(TaskStatus.SUCCESS, error="多余错误")
    with pytest.raises(ValidationError):
        task_record(TaskStatus.FAILURE, error=None)


def test_reload_status_success_without_error_and_failure_with_reason() -> None:
    assert ReloadStatus(revision="sha256:" + "0" * 64, ok=True).ok
    failed = ReloadStatus(
        revision="sha256:" + "0" * 64,
        ok=False,
        error={"group": "history_timestamps", "field": "long_gap_minutes", "reason": "越界"},
    )
    assert failed.ok is False
    with pytest.raises(ValidationError):
        ReloadStatus(revision="sha256:" + "0" * 64, ok=True, error={"reason": "多余"})
    with pytest.raises(ValidationError):
        ReloadStatus(revision="sha256:" + "0" * 64, ok=False)


def runtime_status(
    *,
    session_state: SessionState = SessionState.IDLE,
    last_reload: ReloadStatus | None = None,
    tasks: tuple[BackgroundTaskRecord, ...] = (),
) -> RuntimeStatus:
    reload = last_reload or ReloadStatus(revision="sha256:" + "0" * 64, ok=True)
    failed = any(item.status is TaskStatus.FAILURE for item in tasks)
    health = HealthState.WARNING if (not reload.ok or failed) else HealthState.OK
    return RuntimeStatus(
        session_state=session_state,
        queue_depth=0,
        current_item_id=(
            "item-00000000-0000-0000-0000-000000000006"
            if session_state is SessionState.PROCESSING
            else None
        ),
        tasks=tasks,
        health=health,
        last_reload=reload,
        pending_turn_id=None,
        counters={"chat_turns": 1},
        uptime_seconds=1.0,
    )


def test_runtime_status_state_and_health_invariants() -> None:
    assert runtime_status().health is HealthState.OK
    assert runtime_status(
        session_state=SessionState.PROCESSING
    ).session_state is SessionState.PROCESSING
    with pytest.raises(ValidationError):
        RuntimeStatus(
            session_state=SessionState.PROCESSING,
            queue_depth=0,
            current_item_id=None,
            tasks=(),
            health=HealthState.OK,
            last_reload=ReloadStatus(revision="", ok=True),
            counters={},
            uptime_seconds=0,
        )
    with pytest.raises(ValidationError):
        RuntimeStatus(
            session_state=SessionState.IDLE,
            queue_depth=0,
            current_item_id=None,
            tasks=(),
            health=HealthState.OK,
            last_reload=ReloadStatus(
                revision="",
                ok=False,
                error={"group": "agent", "reason": "失败"},
            ),
            counters={},
            uptime_seconds=0,
        )
