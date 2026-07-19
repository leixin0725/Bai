"""[2026-07-19] 长期记忆模型拒绝无来源、悬空关系、循环替代和覆盖缺口。"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from bai_agent.domain.models import (
    CoverageSpan,
    CreatedBy,
    CurationCheckpoint,
    LongTermMemoryDocument,
    LongTermMemoryItem,
    MemoryCoverageOverview,
    MemoryKind,
    MemoryStatus,
    SourceReference,
    SourceRelation,
)


NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)
HASH = "sha256:" + "1" * 64
REC1 = "rec-00000000-0000-4000-8000-000000000001"


def item(memory_id="mem-00000000-0000-4000-8000-000000000001", **overrides):
    values = dict(
        memory_id=memory_id,
        kind=MemoryKind.FACT,
        text="用户偏好清晰说明",
        status=MemoryStatus.ACTIVE,
        source_refs=(SourceReference(record_id=REC1, relation=SourceRelation.SUPPORTS, record_sha256=HASH),),
        created_by=CreatedBy.MEMORY_CURATOR,
        created_at=NOW,
        updated_at=NOW,
        supersedes=(),
        tags=("communication",),
    )
    values.update(overrides)
    return LongTermMemoryItem(**values)


def test_memory_requires_source_and_valid_status() -> None:
    with pytest.raises(ValidationError):
        item(source_refs=())
    with pytest.raises(ValidationError):
        item(status="unknown")


def test_document_rejects_dangling_supersedes_and_cycles() -> None:
    with pytest.raises(ValidationError):
        LongTermMemoryDocument(
            schema_version=1,
            revision=0,
            curation=CurationCheckpoint.empty(),
            coverage_overview=MemoryCoverageOverview.empty(),
            memories=(item(supersedes=("mem-missing",)),),
        )

    first = item(supersedes=("mem-00000000-0000-4000-8000-000000000002",))
    second = item(
        "mem-00000000-0000-4000-8000-000000000002",
        supersedes=(first.memory_id,),
    )
    with pytest.raises(ValidationError):
        LongTermMemoryDocument(
            schema_version=1,
            revision=0,
            curation=CurationCheckpoint.empty(),
            coverage_overview=MemoryCoverageOverview.empty(),
            memories=(first, second),
        )


def test_coverage_revision_and_span_must_match_frontier() -> None:
    span = CoverageSpan(
        start_sequence=1,
        end_sequence=1,
        batch_id="batch-00000000-0000-4000-8000-000000000001",
        record_ids=(REC1,),
        record_hashes=(HASH,),
    )
    with pytest.raises(ValidationError):
        LongTermMemoryDocument(
            schema_version=1,
            revision=1,
            curation=CurationCheckpoint(
                curated_through_sequence=1,
                last_batch_id=span.batch_id,
                updated_at=NOW,
                covered_record_ids=(REC1,),
            ),
            coverage_overview=MemoryCoverageOverview(revision=0, text="概览", coverage_spans=(span,)),
            memories=(),
        )


def test_manual_active_memory_wins_selection_priority() -> None:
    manual = item(created_by=CreatedBy.MANUAL, text="人工确认的当前事实")
    automatic = item(
        "mem-00000000-0000-4000-8000-000000000002",
        status=MemoryStatus.SUPERSEDED,
        text="旧事实",
    )
    assert manual.is_current
    assert not automatic.is_current
