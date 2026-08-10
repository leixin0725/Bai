"""[2026-07-19] 性能数据集固定规模与内容，使跨进程启动样本可以直接比较。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from bai_agent.domain.models import (
    CoverageSpan,
    CreatedBy,
    CurationCheckpoint,
    LongTermMemoryDocument,
    LongTermMemoryItem,
    MemoryCoverageOverview,
    MemoryKind,
    MemoryStatus,
    RawRecord,
    Role,
    SourceReference,
    SourceRelation,
    canonical_json,
)
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.long_term import LongTermStore
from bai_agent.memory.selection import select_recent_complete_turns
from bai_agent.security.permissions import ensure_private_path


RAW_RECORD_COUNT = 10_000
LONG_TERM_MEMORY_COUNT = 1_000
RECENT_RECORD_COUNT = 48
CURATED_THROUGH = RAW_RECORD_COUNT - RECENT_RECORD_COUNT
SEGMENT_RECORD_COUNT = 256
COVERAGE_SPAN_RECORD_COUNT = 24
FIXED_TIME = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
CONFIG_REVISION = "sha256:" + "7" * 64


@dataclass(frozen=True, slots=True)
class PerformanceDataset:
    data_dir: Path
    memory_root: Path
    raw_record_count: int
    long_term_memory_count: int
    curated_through: int


def _identifier(prefix: str, value: int) -> str:
    return f"{prefix}-{UUID(int=value)}"


def _raw_record(sequence: int) -> RawRecord:
    turn_number = (sequence + 1) // 2
    role = Role.USER if sequence % 2 else Role.ASSISTANT
    return RawRecord.create(
        record_id=_identifier("rec", sequence),
        global_sequence=sequence,
        turn_id=_identifier("turn", turn_number),
        role=role,
        content=f"性能夹具第 {sequence} 条{'用户' if role == Role.USER else '助手'}原始记录。",
        created_at=FIXED_TIME,
        state_id="default",
        config_revision=CONFIG_REVISION,
    )


def prepare_performance_dataset(data_dir: Path) -> PerformanceDataset:
    """[2026-07-19] 造数不走逐条追加路径，避免 O(n²) 准备成本污染启动测量。"""
    memory_root = data_dir / "memory"
    raw_dir = memory_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    ensure_private_path(memory_root, is_directory=True)
    ensure_private_path(raw_dir, is_directory=True)

    records = tuple(_raw_record(sequence) for sequence in range(1, RAW_RECORD_COUNT + 1))
    for segment_number, start in enumerate(range(0, len(records), SEGMENT_RECORD_COUNT), start=1):
        segment_records = records[start : start + SEGMENT_RECORD_COUNT]
        payload = "".join(
            canonical_json(record.model_dump(mode="json")) + "\n"
            for record in segment_records
        ).encode("utf-8")
        path = raw_dir / f"{segment_number:08d}.jsonl"
        path.write_bytes(payload)
        ensure_private_path(path, is_directory=False)

    spans: list[CoverageSpan] = []
    for span_number, start_sequence in enumerate(
        range(1, CURATED_THROUGH + 1, COVERAGE_SPAN_RECORD_COUNT), start=1
    ):
        end_sequence = min(
            start_sequence + COVERAGE_SPAN_RECORD_COUNT - 1, CURATED_THROUGH
        )
        selected = records[start_sequence - 1 : end_sequence]
        spans.append(
            CoverageSpan(
                start_sequence=start_sequence,
                end_sequence=end_sequence,
                batch_id=_identifier("batch", span_number),
                record_ids=tuple(record.record_id for record in selected),
                record_hashes=tuple(record.content_sha256 for record in selected),
            )
        )

    memories = tuple(
        LongTermMemoryItem(
            memory_id=_identifier("mem", index),
            kind=MemoryKind.FACT,
            text=f"性能夹具长期记忆 {index}。",
            status=MemoryStatus.ACTIVE,
            source_refs=(
                SourceReference(
                    record_id=records[index - 1].record_id,
                    relation=SourceRelation.SUPPORTS,
                    record_sha256=records[index - 1].content_sha256,
                ),
            ),
            created_by=CreatedBy.MEMORY_CURATOR,
            created_at=FIXED_TIME,
            updated_at=FIXED_TIME,
            supersedes=(),
            tags=("性能夹具",),
        )
        for index in range(1, LONG_TERM_MEMORY_COUNT + 1)
    )
    revision = len(spans)
    document = LongTermMemoryDocument(
        schema_version=1,
        revision=revision,
        curation=CurationCheckpoint(
            curated_through_sequence=CURATED_THROUGH,
            last_batch_id=spans[-1].batch_id,
            updated_at=FIXED_TIME,
            covered_record_ids=spans[-1].record_ids,
        ),
        coverage_overview=MemoryCoverageOverview(
            revision=revision,
            text="性能夹具中较早的记录已经连续整理，最后 48 条原文仍直接注入。",
            coverage_spans=tuple(spans),
        ),
        memories=memories,
    )
    archive = RawRecordArchive(memory_root)
    store = LongTermStore(memory_root, archive)
    store.commit(document)
    return PerformanceDataset(
        data_dir=data_dir,
        memory_root=memory_root,
        raw_record_count=len(records),
        long_term_memory_count=len(memories),
        curated_through=CURATED_THROUGH,
    )
