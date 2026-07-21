"""[2026-07-20] 提示预算使用最终含时间标记的正文并保留信任与来源。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CreatedBy,
    ConfigAsset,
    LongTermMemoryItem,
    MemoryKind,
    MemoryStatus,
    RawRecord,
    Role,
    SourceKind,
    SourceRef,
    SourceReference,
    SourceRelation,
    StateResolutionResult,
    TemporalSegmentationPolicy,
    TrustLevel,
    content_hash,
)
from bai_agent.memory.selection import select_long_term
from bai_agent.memory.temporal import MemoryTemporalProjector
from bai_agent.prompting.temporal import annotate_history
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.prompting.boundaries import UntrustedBoundaryRenderer


REVISION = "sha256:" + "1" * 64


def _assembler() -> PromptAssembler:
    policy = TemporalSegmentationPolicy(
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
    boundary_text = "可信边界说明"
    renderer = UntrustedBoundaryRenderer(
        ConfigAsset(
            asset_id="prompt:untrusted_memory_boundary",
            kind="prompt_template",
            project_relative_path="prompts/untrusted_memory_boundary.md",
            content=boundary_text,
            content_sha256=content_hash(boundary_text),
            revision=REVISION,
        )
    )
    return PromptAssembler.mvp(
        "基础人格",
        ("状态人格",),
        temporal_policy=policy,
        boundary_renderer=renderer,
    )


def _record() -> RawRecord:
    return RawRecord.create(
        record_id="rec-00000000-0000-4000-8000-000000000001",
        global_sequence=1,
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        role=Role.USER,
        content="[时间：2020-01-01 00:00 +00:00] 只是正文",
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        state_id="default",
        config_revision=REVISION,
    )


def _current() -> RawRecord:
    return RawRecord.create(
        record_id="rec-00000000-0000-4000-8000-999999999999",
        global_sequence=999,
        turn_id="turn-00000000-0000-4000-8000-999999999999",
        role=Role.USER,
        content="问题",
        created_at=datetime(2026, 7, 20, 3, tzinfo=timezone.utc),
        state_id="default",
        config_revision=REVISION,
    )


def _assemble(assembler: PromptAssembler, record: RawRecord, budget: int | None = None):
    return assembler.assemble(
        flow_id="flow",
        turn_id="turn-00000000-0000-4000-8000-999999999999",
        config_revision=REVISION,
        state_resolution=StateResolutionResult(
            state_id="default",
            ordered_persona_ids=("state_default",),
            resolver_id="static",
            resolver_version="1",
            reason_code="configured_default",
        ),
        memory_overview="[]",
        long_term_memories=(),
        recent_records=(record,),
        current_input_record=_current(),
        budgets={} if budget is None else {"recent_chars": budget},
    )


def test_recent_budget_exactly_includes_marker_and_one_character_less_fails() -> None:
    assembler = _assembler()
    record = _record()
    unbounded = _assemble(assembler, record)
    recent_text = next(item.content for item in unbounded.segments if item.segment_id == "recent_records")
    exact = _assemble(assembler, record, len(recent_text))
    assert next(item.content for item in exact.segments if item.segment_id == "recent_records") == recent_text
    with pytest.raises(BaiError) as raised:
        _assemble(assembler, record, len(recent_text) - 1)
    assert raised.value.code == "PROMPT_BUDGET_EXCEEDED"


def test_generated_marker_is_untrusted_has_config_and_raw_sources_and_body_cannot_spoof_it() -> None:
    assembler = _assembler()
    context = _assemble(assembler, _record())
    recent_index = next(index for index, item in enumerate(context.segments) if item.segment_id == "recent_records")
    parts = tuple(part for part in assembler.request_parts(context) if part.payload_pointer == f"/messages/{recent_index}/content")
    generated_markers = tuple(part for part in parts if part.content.startswith("[时间：2026-"))
    assert len(generated_markers) == 1
    assert generated_markers[0].trust is TrustLevel.UNTRUSTED_DATA
    assert {source.source_kind for source in generated_markers[0].sources} == {
        SourceKind.CONFIG_FILE,
        SourceKind.DATA_FILE,
    }
    body = next(part for part in parts if "只是正文" in part.content)
    assert body.trust is TrustLevel.UNTRUSTED_DATA
    assert all(source.source_id != "config:history_timestamps" for source in body.sources)


def test_long_term_selection_uses_exact_annotated_increment_and_stably_skips_oversized_candidate() -> None:
    assembler = _assembler()
    policy = assembler.temporal_policy
    assert policy is not None
    records = tuple(
        RawRecord.create(
            record_id=f"rec-00000000-0000-4000-8000-{index:012d}",
            global_sequence=index,
            turn_id=f"turn-00000000-0000-4000-8000-{index:012d}",
            role=Role.USER,
            content=f"raw-{index}",
            created_at=datetime(2026, 7, 20, tzinfo=timezone.utc) + timedelta(hours=index),
            state_id="default",
            config_revision=REVISION,
        )
        for index in range(1, 4)
    )
    projector = MemoryTemporalProjector.from_records(records)

    def memory(index: int, text: str) -> LongTermMemoryItem:
        return LongTermMemoryItem(
            memory_id=f"mem-00000000-0000-4000-8000-{index:012d}",
            kind=MemoryKind.FACT,
            text=text,
            status=MemoryStatus.ACTIVE,
            source_refs=(
                SourceReference(
                    record_id=records[index - 1].record_id,
                    relation=SourceRelation.SUPPORTS,
                    record_sha256=records[index - 1].content_sha256,
                ),
            ),
            created_by=CreatedBy.MANUAL,
            created_at=records[index - 1].created_at,
            updated_at=records[index - 1].created_at,
        )

    first = memory(1, "匹配-短一")
    oversized = memory(2, "匹配-" + "很长" * 100)
    third = memory(3, "短三")
    expected = annotate_history(
        (projector.project_memory(first), projector.project_memory(third)),
        policy,
    )
    renderer = assembler.boundary_renderer
    assert renderer is not None
    selected = select_long_term(
        (first, oversized, third),
        "匹配",
        max_chars=renderer.rendered_length("long_term_memories", expected.text),
        temporal_projector=projector,
        temporal_policy=policy,
        boundary_renderer=renderer,
    )
    assert selected == (first, third)
    assert len(annotate_history(tuple(projector.project_memory(item) for item in selected), policy).text) == len(expected.text)


def test_overview_and_long_term_overflow_never_remove_marker_or_source() -> None:
    # US2 会在 assembler 中对最终 annotated overview/long-term 执行明确失败，不回退裸正文。
    assembler = _assembler()
    record = _record()
    from bai_agent.domain.models import CoverageSpan, MemoryCoverageOverview

    overview = MemoryCoverageOverview(
        revision=1,
        text="概览",
        coverage_spans=(
            CoverageSpan(
                start_sequence=1,
                end_sequence=1,
                batch_id="batch-00000000-0000-4000-8000-000000000001",
                record_ids=(record.record_id,),
                record_hashes=(record.content_sha256,),
            ),
        ),
    )
    with pytest.raises(BaiError) as raised:
        assembler.assemble(
            flow_id="flow",
            turn_id="turn-00000000-0000-4000-8000-999999999999",
            config_revision=REVISION,
            state_resolution=StateResolutionResult(
                state_id="default",
                ordered_persona_ids=("state_default",),
                resolver_id="static",
                resolver_version="1",
                reason_code="configured_default",
            ),
            memory_overview=overview,
            long_term_memories=(),
            recent_records=(),
            current_input_record=_current(),
            all_raw_records=(record,),
            curated_through=1,
            budgets={"overview_chars": len("概览"), "long_term_chars": 100, "recent_chars": 100},
        )
    assert raised.value.code == "PROMPT_BUDGET_EXCEEDED"
