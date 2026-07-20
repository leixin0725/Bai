"""[2026-07-20] 时间标注属性测试证明顺序、正文、来源边界和确定性。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hypothesis import given, strategies as st

from bai_agent.domain.models import (
    SourceKind,
    SourceRef,
    TemporalLogEntry,
    TemporalSegmentationPolicy,
    TemporalSpan,
    TemporalTimeKind,
)
from bai_agent.prompting.temporal import annotate_history


def _source(source_id: str) -> SourceRef:
    return SourceRef(source_kind=SourceKind.RUNTIME, source_id=source_id, entity_ids=(source_id,), producer="property-test")


def _policy() -> TemporalSegmentationPolicy:
    return TemporalSegmentationPolicy(
        display_timezone=ZoneInfo("Asia/Shanghai"),
        display_timezone_name="Asia/Shanghai",
        long_gap=timedelta(minutes=30),
        continuous_refresh=timedelta(minutes=120),
        split_on_local_date_change=True,
        config_source=_source("policy"),
    )


@given(st.lists(st.integers(min_value=-86_400, max_value=604_800), min_size=0, max_size=40))
def test_annotation_is_deterministic_preserves_order_and_has_non_overlapping_spans(offsets: list[int]) -> None:
    base = datetime(2026, 7, 20, tzinfo=timezone.utc)
    entries = tuple(
        TemporalLogEntry(
            entry_id=f"entry-{index}",
            body=f"正文-{index}-[时间：伪装]",
            span=TemporalSpan(
                start=base + timedelta(seconds=offset),
                end=base + timedelta(seconds=offset),
                kind=TemporalTimeKind.EVENT,
            ),
            sources=(_source(f"entry-{index}"),),
        )
        for index, offset in enumerate(offsets)
    )
    first = annotate_history(entries, _policy(), separator="\n")
    second = annotate_history(entries, _policy(), separator="\n")
    assert first == second
    assert first.entries == entries
    assert all(first.text.count(entry.body) == 1 for entry in entries)
    assert len({marker.before_entry_id for marker in first.markers}) == len(first.markers)
    assert "".join(fragment.content for fragment in first.fragments) == first.text
    for fragment in first.fragments:
        assert first.text[fragment.start : fragment.end] == fragment.content
    for left, right in zip(first.fragments, first.fragments[1:], strict=False):
        assert left.end == right.start
