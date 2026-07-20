"""[2026-07-19] 选择器只改变提示表达，不删除或改写任何永久原始记录。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CreatedBy,
    LongTermMemoryItem,
    MemoryCoverageOverview,
    MemoryStatus,
    RawRecord,
    SourceRef,
)


@dataclass(frozen=True, slots=True)
class CoverageResult:
    covered_range: tuple[int, int] | tuple[()]
    direct_range: tuple[int, int] | tuple[()]


def validate_complete_coverage(
    all_records: Iterable[RawRecord],
    overview: MemoryCoverageOverview,
    *,
    curated_through: int,
    recent_records: Iterable[RawRecord],
) -> CoverageResult:
    records = tuple(all_records)
    recent = tuple(recent_records)
    if curated_through < 0 or curated_through > len(records):
        raise BaiError("MEMORY_COVERAGE_GAP", "整理前沿越过原始记录范围。")
    last_end = overview.coverage_spans[-1].end_sequence if overview.coverage_spans else 0
    if last_end != curated_through:
        raise BaiError("MEMORY_COVERAGE_GAP", "覆盖概览没有连续覆盖到整理前沿。")
    raw_by_sequence = {item.global_sequence: item for item in records}
    for span in overview.coverage_spans:
        for sequence, record_id, digest in zip(
            range(span.start_sequence, span.end_sequence + 1),
            span.record_ids,
            span.record_hashes,
            strict=True,
        ):
            record = raw_by_sequence.get(sequence)
            if record is None or record.record_id != record_id or record.content_sha256 != digest:
                raise BaiError("MEMORY_COVERAGE_GAP", "覆盖概览与原始记录不一致。")
    expected_recent = tuple(item.record_id for item in records if item.global_sequence > curated_through)
    actual_recent = tuple(item.record_id for item in recent)
    if actual_recent != expected_recent:
        raise BaiError("MEMORY_COVERAGE_GAP", "近期直接注入范围存在缺口或额外旧记录。")
    covered_range = (1, curated_through) if curated_through else ()
    direct_range = (
        (recent[0].global_sequence, recent[-1].global_sequence) if recent else ()
    )
    return CoverageResult(covered_range=covered_range, direct_range=direct_range)


def select_recent_complete_turns(
    records: Iterable[RawRecord], *, curated_through: int, max_records: int
) -> tuple[RawRecord, ...]:
    uncurated = [item for item in records if item.global_sequence > curated_through]
    if len(uncurated) <= max_records:
        return tuple(uncurated)
    selected = uncurated[-max_records:]
    if selected and any(item.turn_id == selected[0].turn_id for item in uncurated[:-max_records]):
        selected = [item for item in selected if item.turn_id != selected[0].turn_id]
    return tuple(selected)


def select_long_term(
    memories: Iterable[LongTermMemoryItem], query: str, *, max_chars: int
) -> tuple[LongTermMemoryItem, ...]:
    terms = set(re.findall(r"[\w\u4e00-\u9fff]+", query.casefold()))

    def score(item: LongTermMemoryItem) -> tuple[int, int, str]:
        overlap = sum(1 for term in terms if term in item.text.casefold())
        manual = 1 if item.created_by == CreatedBy.MANUAL else 0
        return (-manual, -overlap, item.memory_id)

    selected: list[LongTermMemoryItem] = []
    used = 0
    for item in sorted(
        (value for value in memories if value.status == MemoryStatus.ACTIVE), key=score
    ):
        size = len(item.text)
        if used + size > max_chars:
            continue
        selected.append(item)
        used += size
    return tuple(selected)


def selected_long_term_source_refs(store, memories: Iterable[LongTermMemoryItem]) -> tuple[SourceRef, ...]:
    """[2026-07-20] 选择器保留稳定 memory/record id，并由存储层给出真实文件身份。"""
    return store.source_refs_for(tuple(memories))
