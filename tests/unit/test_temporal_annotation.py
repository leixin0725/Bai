"""[2026-07-20] 统一时间分段器覆盖成功、临界和关键异常路径。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bai_agent.domain.models import (
    AnnotatedFragmentKind,
    SourceKind,
    SourceRef,
    TemporalLogEntry,
    TemporalMarkerReason,
    TemporalSegmentationPolicy,
    TemporalSpan,
    TemporalTimeKind,
)
from bai_agent.prompting.temporal import annotate_history, format_temporal_marker


def _source(source_id: str) -> SourceRef:
    return SourceRef(
        source_kind=SourceKind.RUNTIME,
        source_id=source_id,
        entity_ids=(source_id,),
        producer="temporal-test",
    )


def _policy(
    *,
    zone: str = "Asia/Shanghai",
    gap: int = 30,
    refresh: int = 120,
    split_on_date: bool = True,
) -> TemporalSegmentationPolicy:
    return TemporalSegmentationPolicy(
        display_timezone=ZoneInfo(zone),
        display_timezone_name=zone,
        long_gap=timedelta(minutes=gap),
        continuous_refresh=timedelta(minutes=refresh),
        split_on_local_date_change=split_on_date,
        config_source=_source("config:history-timestamps"),
    )


def _entry(entry_id: str, start: datetime, body: str | None = None, end: datetime | None = None) -> TemporalLogEntry:
    return TemporalLogEntry(
        entry_id=entry_id,
        body=body or entry_id,
        span=TemporalSpan(start=start, end=end or start, kind=TemporalTimeKind.EVENT if end is None else TemporalTimeKind.SOURCE_RANGE),
        sources=(_source(entry_id),),
    )


def test_empty_and_single_entry_have_no_or_one_first_marker() -> None:
    policy = _policy()
    assert annotate_history((), policy).text == ""
    result = annotate_history((_entry("one", datetime(2026, 7, 20, 1, tzinfo=timezone.utc)),), policy)
    assert result.text == "[时间：2026-07-20 09:00 +08:00]\none"
    assert len(result.markers) == 1
    assert result.markers[0].reasons == frozenset({TemporalMarkerReason.FIRST})


def test_dense_history_is_sparse_and_preserves_body_order() -> None:
    base = datetime(2026, 7, 20, tzinfo=timezone.utc)
    entries = tuple(_entry(f"e-{index}", base + timedelta(seconds=index * 12), f"正文-{index}") for index in range(100))
    result = annotate_history(entries, _policy())
    assert len(result.markers) == 1
    assert [entry.body for entry in result.entries] == [entry.body for entry in entries]
    assert all(result.text.index(entry.body) < result.text.index(entries[index + 1].body) for index, entry in enumerate(entries[:-1]))


def test_gap_exact_boundary_and_one_microsecond_below() -> None:
    base = datetime(2026, 7, 20, tzinfo=timezone.utc)
    below = annotate_history(
        (_entry("a", base), _entry("b", base + timedelta(minutes=30) - timedelta(microseconds=1))),
        _policy(),
    )
    exact = annotate_history((_entry("a", base), _entry("b", base + timedelta(minutes=30))), _policy())
    assert len(below.markers) == 1
    assert len(exact.markers) == 2
    assert TemporalMarkerReason.LONG_GAP in exact.markers[1].reasons


def test_refresh_exact_boundary_resets_and_never_emits_tail_marker() -> None:
    base = datetime(2026, 7, 20, tzinfo=timezone.utc)
    entries = (
        _entry("a", base),
        _entry("b", base + timedelta(minutes=119)),
        _entry("c", base + timedelta(minutes=120)),
        _entry("d", base + timedelta(minutes=239)),
        _entry("e", base + timedelta(minutes=240)),
    )
    result = annotate_history(entries, _policy(gap=120, refresh=120))
    assert [marker.before_entry_id for marker in result.markers] == ["a", "c", "e"]
    assert all(TemporalMarkerReason.REFRESH in marker.reasons for marker in result.markers[1:])


def test_overlap_touch_same_instant_and_reversal_keep_input_order() -> None:
    base = datetime(2026, 7, 20, tzinfo=timezone.utc)
    entries = (
        _entry("range", base, end=base + timedelta(hours=2)),
        _entry("overlap", base + timedelta(hours=1), end=base + timedelta(hours=2)),
        _entry("touch", base + timedelta(hours=2)),
        _entry("same", base + timedelta(hours=2)),
        _entry("reverse", base + timedelta(minutes=5)),
    )
    result = annotate_history(entries, _policy(gap=30, refresh=600, split_on_date=False))
    assert [entry.entry_id for entry in result.entries] == [entry.entry_id for entry in entries]
    assert [marker.before_entry_id for marker in result.markers] == ["range", "reverse"]
    assert result.markers[-1].reasons == frozenset({TemporalMarkerReason.TIME_REVERSAL})


def test_local_date_change_can_be_enabled_or_disabled_and_reasons_merge() -> None:
    first = datetime(2026, 7, 20, 15, 59, tzinfo=timezone.utc)
    second = first + timedelta(minutes=2)
    entries = (_entry("a", first), _entry("b", second))
    enabled = annotate_history(entries, _policy(gap=1, refresh=1, split_on_date=True))
    disabled = annotate_history(entries, _policy(gap=10, refresh=10, split_on_date=False))
    assert enabled.markers[1].reasons == frozenset(
        {
            TemporalMarkerReason.LONG_GAP,
            TemporalMarkerReason.LOCAL_DATE_CHANGE,
            TemporalMarkerReason.REFRESH,
        }
    )
    assert len(enabled.markers) == 2
    assert len(disabled.markers) == 1


def test_fixed_event_range_recorded_and_dst_offset_formats() -> None:
    shanghai = _policy()
    instant = datetime(2026, 7, 20, tzinfo=timezone.utc)
    assert format_temporal_marker(TemporalSpan(start=instant, end=instant, kind=TemporalTimeKind.EVENT), shanghai) == "[时间：2026-07-20 08:00 +08:00]"
    assert format_temporal_marker(TemporalSpan(start=instant, end=instant, kind=TemporalTimeKind.SOURCE_RANGE), shanghai) == "[时间范围：2026-07-20 08:00 +08:00 至 2026-07-20 08:00 +08:00]"
    assert format_temporal_marker(TemporalSpan(start=instant, end=instant, kind=TemporalTimeKind.RECORDED), shanghai) == "[记录时间：2026-07-20 08:00 +08:00]"

    new_york = _policy(zone="America/New_York", gap=60, refresh=120)
    before = _entry("before", datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))
    after = _entry("after", datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))
    result = annotate_history((before, after), new_york)
    assert "2026-11-01 01:30 -04:00" in result.markers[0].text
    assert "2026-11-01 01:30 -05:00" in result.markers[1].text


def test_marker_body_and_separator_fragments_cover_text_without_overlap() -> None:
    base = datetime(2026, 7, 20, tzinfo=timezone.utc)
    result = annotate_history((_entry("a", base), _entry("b", base + timedelta(minutes=1))), _policy(), separator="\n---\n")
    assert {fragment.kind for fragment in result.fragments} == {
        AnnotatedFragmentKind.MARKER,
        AnnotatedFragmentKind.BODY,
        AnnotatedFragmentKind.SEPARATOR,
    }
    assert "".join(fragment.content for fragment in result.fragments) == result.text
    for left, right in zip(result.fragments, result.fragments[1:], strict=False):
        assert left.end == right.start
