"""[2026-07-20] 记忆时间投影只使用单次 raw 快照和可验证来源范围。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CoverageSpan,
    CreatedBy,
    LongTermMemoryDocument,
    LongTermMemoryItem,
    MemoryCoverageOverview,
    MemoryKind,
    MemoryStatus,
    RawRecord,
    Role,
    SourceReference,
    SourceRelation,
    TemporalTimeKind,
)
from bai_agent.memory.temporal import MemoryTemporalProjector


REVISION = "sha256:" + "1" * 64
BASE = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _raw(index: int, minute: int) -> RawRecord:
    return RawRecord.create(
        record_id=f"rec-00000000-0000-4000-8000-{index:012d}",
        global_sequence=index,
        turn_id=f"turn-00000000-0000-4000-8000-{index:012d}",
        role=Role.USER,
        content=f"raw-{index}",
        created_at=BASE + timedelta(minutes=minute),
        state_id="default",
        config_revision=REVISION,
    )


def _memory(index: int, refs: tuple[SourceReference, ...], text: str | None = None) -> LongTermMemoryItem:
    return LongTermMemoryItem(
        memory_id=f"mem-00000000-0000-4000-8000-{index:012d}",
        kind=MemoryKind.FACT,
        text=text or f"memory-{index}",
        status=MemoryStatus.ACTIVE,
        source_refs=refs,
        created_by=CreatedBy.MEMORY_CURATOR,
        created_at=BASE + timedelta(days=1),
        updated_at=BASE + timedelta(days=1),
    )


def _ref(record: RawRecord, digest: str | None = None) -> SourceReference:
    return SourceReference(
        record_id=record.record_id,
        relation=SourceRelation.SUPPORTS,
        record_sha256=digest or record.content_sha256,
    )


def test_raw_event_and_memory_ranges_use_all_unordered_duplicate_sources() -> None:
    records = (_raw(1, 90), _raw(2, 10), _raw(3, 50))
    projector = MemoryTemporalProjector.from_records(records)
    raw_entry = projector.project_raw(records[0], body="user: raw-1")
    assert raw_entry.span.kind is TemporalTimeKind.EVENT
    assert raw_entry.span.start == records[0].created_at

    item = _memory(1, (_ref(records[0]), _ref(records[1]), _ref(records[1]), _ref(records[2])))
    projected = projector.project_memory(item)
    assert projected.body == item.text
    assert projected.span.kind is TemporalTimeKind.SOURCE_RANGE
    assert projected.span.start == records[1].created_at
    assert projected.span.end == records[0].created_at
    assert {source.source_id for source in projected.sources} >= {
        f"memory:{item.memory_id}",
        *(f"raw:{record.record_id}" for record in records),
    }


def test_overview_uses_all_coverage_records_and_equal_endpoint_stays_range() -> None:
    record = _raw(1, 15)
    projector = MemoryTemporalProjector.from_records((record,))
    overview = MemoryCoverageOverview(
        revision=1,
        text="overview",
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
    entry = projector.project_overview(overview)
    assert entry is not None
    assert entry.span.kind is TemporalTimeKind.SOURCE_RANGE
    assert entry.span.start == entry.span.end == record.created_at


@pytest.mark.parametrize("failure", ["missing", "hash"])
def test_missing_and_hash_mismatch_fail_without_recorded_fallback(failure: str) -> None:
    record = _raw(1, 0)
    projector = MemoryTemporalProjector.from_records((record,))
    source = (
        SourceReference(
            record_id="rec-00000000-0000-4000-8000-999999999999",
            relation=SourceRelation.SUPPORTS,
            record_sha256=record.content_sha256,
        )
        if failure == "missing"
        else _ref(record, "sha256:" + "0" * 64)
    )
    with pytest.raises(BaiError) as raised:
        projector.project_memory(_memory(1, (source,)))
    assert raised.value.code == ("SOURCE_RECORD_MISSING" if failure == "missing" else "SOURCE_HASH_MISMATCH")
    assert not hasattr(projector, "project_recorded")


def test_invalid_raw_time_is_temporal_error_and_unrecognized_schema_is_rejected() -> None:
    invalid = _raw(1, 0).model_copy(update={"created_at": datetime(2026, 7, 20)})
    projector = MemoryTemporalProjector.from_records((invalid,))
    with pytest.raises(BaiError) as raised:
        projector.project_raw(invalid)
    assert raised.value.code == "TEMPORAL_ENTRY_INVALID"

    with pytest.raises(ValidationError):
        LongTermMemoryDocument.model_validate(
            {
                **LongTermMemoryDocument.empty().model_dump(mode="python"),
                "schema_version": 0,
            }
        )
