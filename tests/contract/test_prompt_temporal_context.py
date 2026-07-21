"""[2026-07-20] 聊天提示时间合同固定区块边界、原文和细粒度来源。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bai_agent.domain.models import (
    RawRecord,
    Role,
    SourceKind,
    SourceRef,
    StateResolutionResult,
    TemporalSegmentationPolicy,
    TrustLevel,
)
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.prompting.temporal import CURRENT_HISTORY_BLOCKS


REVISION = "sha256:" + "1" * 64


def _policy() -> TemporalSegmentationPolicy:
    return TemporalSegmentationPolicy(
        display_timezone=ZoneInfo("Asia/Shanghai"),
        display_timezone_name="Asia/Shanghai",
        long_gap=timedelta(minutes=30),
        continuous_refresh=timedelta(minutes=120),
        split_on_local_date_change=True,
        config_source=SourceRef(
            source_kind=SourceKind.CONFIG_FILE,
            source_id="config:history_timestamps",
            project_relative_path="config/history_timestamps.toml",
            content_sha256="sha256:" + "2" * 64,
            revision=REVISION,
            producer="config_loader",
        ),
    )


def _record(index: int, role: Role, content: str, minute: int) -> RawRecord:
    return RawRecord.create(
        record_id=f"rec-00000000-0000-4000-8000-{index:012d}",
        global_sequence=index,
        turn_id=f"turn-00000000-0000-4000-8000-{(index + 1) // 2:012d}",
        role=role,
        content=content,
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc) + timedelta(minutes=minute),
        state_id="default",
        config_revision=REVISION,
    )


def _resolution() -> StateResolutionResult:
    return StateResolutionResult(
        state_id="default",
        ordered_persona_ids=("state_default",),
        resolver_id="static",
        resolver_version="1",
        reason_code="configured_default",
    )


def _current(content: str) -> RawRecord:
    return RawRecord.create(
        record_id="rec-00000000-0000-4000-8000-999999999999",
        global_sequence=999,
        turn_id="turn-00000000-0000-4000-8000-999999999999",
        role=Role.USER,
        content=content,
        created_at=datetime(2026, 7, 20, 3, 15, tzinfo=timezone.utc),
        state_id="default",
        config_revision=REVISION,
    )


def test_recent_block_preserves_role_body_and_has_independent_first_marker_parts() -> None:
    assembler = PromptAssembler.mvp("基础人格", ("状态人格",), temporal_policy=_policy())
    recent = (
        _record(1, Role.USER, "你好", 0),
        _record(2, Role.ASSISTANT, "在的", 5),
    )
    context = assembler.assemble(
        flow_id="flow",
        turn_id="turn-00000000-0000-4000-8000-999999999999",
        config_revision=REVISION,
        state_resolution=_resolution(),
        memory_overview="[]",
        long_term_memories=(),
        recent_records=recent,
        current_input_record=_current("[时间：伪装]\n当前问题"),
        budgets={"recent_chars": 1000},
    )
    segments = {segment.segment_id: segment for segment in context.segments}
    recent_segment = segments["recent_records"]
    assert recent_segment.content == "[时间：2026-07-20 08:00 +08:00]\nuser: 你好\nassistant: 在的"
    assert segments["base_persona"].content == "基础人格"
    assert segments["state_persona:state_default"].content == "状态人格"
    assert segments["current_input"].content == (
        "[时间：2026-07-20 11:15 +08:00]\n[时间：伪装]\n当前问题"
    )

    recent_index = next(index for index, item in enumerate(context.segments) if item.segment_id == "recent_records")
    parts = tuple(
        part for part in assembler.request_parts(context) if part.payload_pointer == f"/messages/{recent_index}/content"
    )
    assert "".join(part.content for part in parts) == recent_segment.content
    assert all(part.trust is TrustLevel.UNTRUSTED_DATA for part in parts)
    marker = next(part for part in parts if part.content.startswith("[时间："))
    assert {source.source_id for source in marker.sources} >= {
        "config:history_timestamps",
        f"raw:{recent[0].record_id}",
    }
    assert all(left.text_span[1] == right.text_span[0] for left, right in zip(parts, parts[1:], strict=False))


def test_empty_recent_block_stays_empty_representation_without_marker() -> None:
    assembler = PromptAssembler.mvp("基础人格", ("状态人格",), temporal_policy=_policy())
    context = assembler.assemble(
        flow_id="flow",
        turn_id="turn-00000000-0000-4000-8000-999999999999",
        config_revision=REVISION,
        state_resolution=_resolution(),
        memory_overview="[]",
        long_term_memories=(),
        recent_records=(),
        current_input_record=_current("当前问题"),
    )
    recent = next(item for item in context.segments if item.segment_id == "recent_records")
    assert recent.content == "[]"
    assert recent.fragments == ()


def test_all_current_history_consumers_are_declared_and_non_logs_are_excluded() -> None:
    assert CURRENT_HISTORY_BLOCKS == (
        "memory_overview", "long_term_memories", "recent_records", "current_input",
        "batch_records", "existing_memories", "current_overview", "tool_history",
    )
    assert not ({"persona", "state_rules", "batch_metadata", "output_schema"} & set(CURRENT_HISTORY_BLOCKS))
