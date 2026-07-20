"""[2026-07-20] 统一时间标注与来源投影保持线性并满足 10k 主平台门禁。"""

from datetime import timedelta
from time import perf_counter
from zoneinfo import ZoneInfo

import pytest

from bai_agent.domain.models import (
    CreatedBy, LongTermMemoryItem, MemoryKind, MemoryStatus, SourceKind, SourceRef,
    SourceReference, SourceRelation, TemporalLogEntry, TemporalSegmentationPolicy,
    TemporalSpan, TemporalTimeKind,
)
from bai_agent.memory.temporal import MemoryTemporalProjector
from bai_agent.prompting.temporal import annotate_history
from tests.fixtures.performance import CONFIG_REVISION, FIXED_TIME, _identifier, _raw_record


def _policy() -> TemporalSegmentationPolicy:
    return TemporalSegmentationPolicy(
        display_timezone=ZoneInfo("Asia/Shanghai"), display_timezone_name="Asia/Shanghai",
        long_gap=timedelta(minutes=30), continuous_refresh=timedelta(minutes=120),
        split_on_local_date_change=True,
        config_source=SourceRef(
            source_kind=SourceKind.CONFIG_FILE, source_id="config:history_timestamps",
            project_relative_path="config/history_timestamps.toml",
            content_sha256="sha256:" + "2" * 64, revision=CONFIG_REVISION,
            producer="config_loader",
        ),
    )


@pytest.mark.performance
def test_ten_thousand_entries_are_under_one_second_and_repeat_exactly() -> None:
    source = SourceRef(
        source_kind=SourceKind.RUNTIME, source_id="performance-events",
        entity_ids=("performance-events",), producer="performance_fixture",
    )
    entries = tuple(
        TemporalLogEntry(
            entry_id=f"event-{index}", body=f"正文-{index}",
            span=TemporalSpan(
                start=FIXED_TIME + timedelta(minutes=index),
                end=FIXED_TIME + timedelta(minutes=index),
                kind=TemporalTimeKind.EVENT,
            ),
            sources=(source,),
        )
        for index in range(10_000)
    )
    policy = _policy()
    expected = annotate_history(entries, policy)
    started = perf_counter()
    measured = annotate_history(entries, policy)
    elapsed = perf_counter() - started
    assert elapsed < 1.0
    assert measured.text == expected.text
    assert measured.text.startswith("[时间：2026-07-19 08:00 +08:00]\n正文-0")
    assert measured.text.endswith("正文-9999")
    assert len(measured.entries) == 10_000
    for _ in range(100):
        repeated = annotate_history(entries, policy)
        assert repeated.text == expected.text
        assert repeated.fragments == expected.fragments
        assert repeated.markers == expected.markers


@pytest.mark.performance
def test_ten_thousand_raw_and_one_thousand_memories_use_one_linear_index() -> None:
    records = tuple(_raw_record(index) for index in range(1, 10_001))

    class OnePassRecords:
        def __init__(self, values) -> None:
            self.values = values
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            return iter(self.values)

    source = OnePassRecords(records)
    memories = tuple(
        LongTermMemoryItem(
            memory_id=_identifier("mem", index), kind=MemoryKind.FACT,
            text=f"长期记忆-{index}", status=MemoryStatus.ACTIVE,
            source_refs=(SourceReference(
                record_id=records[index - 1].record_id,
                relation=SourceRelation.SUPPORTS,
                record_sha256=records[index - 1].content_sha256,
            ),),
            created_by=CreatedBy.MEMORY_CURATOR, created_at=FIXED_TIME,
            updated_at=FIXED_TIME, tags=("performance",),
        )
        for index in range(1, 1_001)
    )
    started = perf_counter()
    projector = MemoryTemporalProjector.from_records(source)
    projected = tuple(projector.project_memory(memory) for memory in memories)
    elapsed = perf_counter() - started
    assert source.iterations == 1
    assert len(projector.raw_by_id) == 10_000
    assert len(projected) == 1_000
    assert all(item.span.kind is TemporalTimeKind.SOURCE_RANGE for item in projected)
    assert elapsed < 1.0
