"""[2026-07-19] 上下文选择证明所有序列由概览或近期窗口无缺口表示。"""

from pathlib import Path
from datetime import datetime, timezone

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CoverageSpan,
    CreatedBy,
    LongTermMemoryItem,
    MemoryCoverageOverview,
    MemoryKind,
    MemoryStatus,
    Role,
    SourceReference,
    SourceRelation,
)
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.selection import select_long_term, validate_complete_coverage


REVISION = "sha256:" + "1" * 64


def raw(tmp_path: Path, count: int):
    archive = RawRecordArchive(tmp_path)
    for index in range(count):
        archive.append(
            role=Role.USER,
            content=f"记录-{index}",
            turn_id=f"turn-00000000-0000-4000-8000-{index:012d}",
            state_id="default",
            config_revision=REVISION,
        )
    return archive.read_all()


def span(records, start, end, batch_suffix=1):
    chosen = records[start - 1 : end]
    return CoverageSpan(
        start_sequence=start,
        end_sequence=end,
        batch_id=f"batch-00000000-0000-4000-8000-{batch_suffix:012d}",
        record_ids=tuple(item.record_id for item in chosen),
        record_hashes=tuple(item.content_sha256 for item in chosen),
    )


def test_coverage_plus_recent_window_represents_every_record(tmp_path: Path) -> None:
    records = raw(tmp_path, 6)
    overview = MemoryCoverageOverview(revision=1, text="有界概览", coverage_spans=(span(records, 1, 4),))
    result = validate_complete_coverage(records, overview, curated_through=4, recent_records=records[4:])
    assert result.covered_range == (1, 4)
    assert result.direct_range == (5, 6)


def test_gap_overlap_and_missing_recent_record_fail_closed(tmp_path: Path) -> None:
    records = raw(tmp_path, 6)
    with pytest.raises((BaiError, ValueError)):
        MemoryCoverageOverview(
            revision=1,
            text="有缺口",
            coverage_spans=(span(records, 1, 2, 1), span(records, 4, 4, 2)),
        )
    overview = MemoryCoverageOverview(revision=1, text="概览", coverage_spans=(span(records, 1, 4),))
    with pytest.raises(BaiError) as raised:
        validate_complete_coverage(records, overview, curated_through=4, recent_records=records[5:])
    assert raised.value.code == "MEMORY_COVERAGE_GAP"


def test_empty_extraction_span_is_still_traceable(tmp_path: Path) -> None:
    records = raw(tmp_path, 2)
    overview = MemoryCoverageOverview(revision=1, text="无长期要点", coverage_spans=(span(records, 1, 2),))
    assert overview.coverage_spans[0].record_ids == tuple(item.record_id for item in records)
    assert len(overview.text) <= 12000


def test_manual_current_fact_precedes_conflicting_automatic_history(tmp_path: Path) -> None:
    records = raw(tmp_path, 1)
    source = SourceReference(
        record_id=records[0].record_id,
        relation=SourceRelation.SUPPORTS,
        record_sha256=records[0].content_sha256,
    )
    now = datetime(2026, 7, 19, tzinfo=timezone.utc)
    automatic = LongTermMemoryItem(
        memory_id="mem-00000000-0000-4000-8000-000000000001",
        kind=MemoryKind.FACT,
        text="旧偏好",
        status=MemoryStatus.SUPERSEDED,
        source_refs=(source,),
        created_by=CreatedBy.MEMORY_CURATOR,
        created_at=now,
        updated_at=now,
    )
    manual = LongTermMemoryItem(
        memory_id="mem-00000000-0000-4000-8000-000000000002",
        kind=MemoryKind.FACT,
        text="当前偏好",
        status=MemoryStatus.ACTIVE,
        source_refs=(source,),
        created_by=CreatedBy.MANUAL,
        created_at=now,
        updated_at=now,
    )
    assert select_long_term((automatic, manual), "偏好", max_chars=100) == (manual,)
