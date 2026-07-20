"""[2026-07-20] 领域 DTO 的稳定性测试覆盖序列化、时间语义和拒绝边界。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from bai_agent.domain.models import (
    AnnotatedFragment,
    AnnotatedFragmentKind,
    AnnotatedHistory,
    RawRecord,
    Role,
    SourceKind,
    SourceRef,
    TemporalLogEntry,
    TemporalMarker,
    TemporalMarkerReason,
    TemporalSegmentationPolicy,
    TemporalSpan,
    TemporalTimeKind,
    TrustLevel,
    canonical_json,
    content_hash,
    new_id,
    utc_now,
)


def _source(source_id: str = "runtime:test") -> SourceRef:
    return SourceRef(
        source_kind=SourceKind.RUNTIME,
        source_id=source_id,
        entity_ids=(source_id,),
        producer="test",
    )


def test_raw_record_is_frozen_and_json_round_trips() -> None:
    created = datetime(2026, 7, 19, tzinfo=timezone.utc)
    record = RawRecord.create(
        record_id="rec-00000000-0000-4000-8000-000000000001",
        global_sequence=1,
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        role=Role.USER,
        content="你好\n世界",
        created_at=created,
        state_id="default",
        config_revision="sha256:" + "1" * 64,
    )

    restored = RawRecord.model_validate_json(record.model_dump_json())
    assert restored == record
    assert restored.content_sha256 == content_hash("你好\n世界")
    with pytest.raises(ValidationError):
        record.content = "不能修改"  # type: ignore[misc]  # [2026-07-19] 测试冻结赋值边界。


@pytest.mark.parametrize("prefix", ["rec", "turn", "flow", "mem", "batch"])
def test_generated_ids_have_stable_prefix(prefix: str) -> None:
    assert new_id(prefix).startswith(f"{prefix}-")


def test_utc_clock_and_canonical_json_are_deterministic() -> None:
    assert utc_now().utcoffset() == timezone.utc.utcoffset(None)
    assert canonical_json({"乙": 2, "甲": 1}) == '{"乙":2,"甲":1}'


def test_invalid_role_and_naive_time_are_rejected() -> None:
    payload = {
        "schema_version": 1,
        "record_id": "rec-00000000-0000-4000-8000-000000000001",
        "global_sequence": 1,
        "turn_id": "turn-00000000-0000-4000-8000-000000000001",
        "role": "tool",
        "content": "文本",
        "created_at": "2026-07-19T00:00:00",
        "state_id": "default",
        "config_revision": "sha256:" + "1" * 64,
        "content_sha256": content_hash("文本"),
    }
    with pytest.raises(ValidationError):
        RawRecord.model_validate(payload)


def test_temporal_value_objects_are_frozen_and_preserve_explicit_kind() -> None:
    instant = datetime(2026, 7, 20, 1, 2, 3, tzinfo=timezone.utc)
    source = _source()
    span = TemporalSpan(start=instant, end=instant, kind=TemporalTimeKind.SOURCE_RANGE)
    entry = TemporalLogEntry(entry_id="entry-1", body="原文", span=span, sources=(source,))
    policy = TemporalSegmentationPolicy(
        display_timezone=ZoneInfo("Asia/Shanghai"),
        display_timezone_name="Asia/Shanghai",
        long_gap=timedelta(minutes=30),
        continuous_refresh=timedelta(minutes=120),
        split_on_local_date_change=True,
        config_source=source,
    )

    assert span.kind is TemporalTimeKind.SOURCE_RANGE
    assert policy.display_timezone.key == "Asia/Shanghai"
    with pytest.raises(ValidationError):
        entry.body = "被修改"  # type: ignore[misc]  # [2026-07-20] 验证时间项冻结边界。


@pytest.mark.parametrize(
    ("start", "end", "kind"),
    [
        (datetime(2026, 7, 20), datetime(2026, 7, 20), TemporalTimeKind.EVENT),
        (
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 19, tzinfo=timezone.utc),
            TemporalTimeKind.SOURCE_RANGE,
        ),
        (
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 20, 0, 1, tzinfo=timezone.utc),
            TemporalTimeKind.RECORDED,
        ),
    ],
)
def test_temporal_span_rejects_naive_reversed_and_non_point_semantics(
    start: datetime,
    end: datetime,
    kind: TemporalTimeKind,
) -> None:
    with pytest.raises(ValidationError):
        TemporalSpan(start=start, end=end, kind=kind)


def test_temporal_entries_require_sources_and_annotated_history_rejects_duplicate_ids() -> None:
    instant = datetime(2026, 7, 20, tzinfo=timezone.utc)
    span = TemporalSpan(start=instant, end=instant, kind=TemporalTimeKind.EVENT)
    with pytest.raises(ValidationError):
        TemporalLogEntry(entry_id="entry-1", body="原文", span=span, sources=())

    entry = TemporalLogEntry(entry_id="entry-1", body="原文", span=span, sources=(_source(),))
    with pytest.raises(ValidationError):
        AnnotatedHistory(text="", entries=(entry, entry), fragments=(), markers=())


def test_marker_and_fragment_validate_sources_trust_and_exact_span() -> None:
    instant = datetime(2026, 7, 20, tzinfo=timezone.utc)
    span = TemporalSpan(start=instant, end=instant, kind=TemporalTimeKind.EVENT)
    source = _source()
    marker = TemporalMarker(
        before_entry_id="entry-1",
        text="[时间：2026-07-20 08:00 +08:00]",
        reasons=frozenset({TemporalMarkerReason.FIRST}),
        span=span,
        sources=(source,),
        trust=TrustLevel.UNTRUSTED_DATA,
    )
    fragment = AnnotatedFragment(
        fragment_id="entry-1:marker",
        kind=AnnotatedFragmentKind.MARKER,
        entry_id="entry-1",
        start=0,
        end=len(marker.text),
        content=marker.text,
        sources=(source,),
        trust=TrustLevel.UNTRUSTED_DATA,
    )
    entry = TemporalLogEntry(entry_id="entry-1", body="原文", span=span, sources=(source,))
    history = AnnotatedHistory(
        text=marker.text,
        entries=(entry,),
        fragments=(fragment,),
        markers=(marker,),
    )
    assert history.fragments[0].content == history.text[fragment.start : fragment.end]
    with pytest.raises(ValidationError):
        fragment.start = 1  # type: ignore[misc]  # [2026-07-20] 验证片段冻结边界。
