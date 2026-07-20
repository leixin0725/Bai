"""[2026-07-20] 纯时间分段器只处理已验证日志项，不读取存储、模板或 wall clock。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from bai_agent.domain.models import (
    AnnotatedFragment,
    AnnotatedFragmentKind,
    AnnotatedHistory,
    SourceRef,
    TemporalLogEntry,
    TemporalMarker,
    TemporalMarkerReason,
    TemporalSegmentationPolicy,
    TemporalSpan,
    TemporalTimeKind,
    TrustLevel,
)


CURRENT_HISTORY_BLOCKS = (
    "memory_overview",
    "long_term_memories",
    "recent_records",
    "batch_records",
    "existing_memories",
    "current_overview",
    "tool_history",
)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _format_instant(value: datetime, policy: TemporalSegmentationPolicy) -> str:
    local = value.astimezone(policy.display_timezone)
    compact = local.strftime("%Y-%m-%d %H:%M %z")
    return f"{compact[:-2]}:{compact[-2:]}"


def format_temporal_marker(span: TemporalSpan, policy: TemporalSegmentationPolicy) -> str:
    """[2026-07-20] 固定中文模板保留显式 EVENT/RANGE/RECORDED 语义。"""
    start = _format_instant(span.start, policy)
    if span.kind is TemporalTimeKind.EVENT:
        return f"[时间：{start}]"
    if span.kind is TemporalTimeKind.RECORDED:
        return f"[记录时间：{start}]"
    end = _format_instant(span.end, policy)
    return f"[时间范围：{start} 至 {end}]"


def _marker_sources(policy: TemporalSegmentationPolicy, entry: TemporalLogEntry) -> tuple[SourceRef, ...]:
    result: list[SourceRef] = []
    seen: set[tuple[object, ...]] = set()
    for source in (policy.config_source, *entry.sources):
        identity = (
            source.source_kind,
            source.source_id,
            source.project_relative_path,
            source.content_sha256,
            source.revision,
            source.entity_ids,
            source.producer,
        )
        if identity not in seen:
            seen.add(identity)
            result.append(source)
    return tuple(result)


def annotate_history(
    entries: Iterable[TemporalLogEntry],
    policy: TemporalSegmentationPolicy,
    separator: str = "\n",
) -> AnnotatedHistory:
    """[2026-07-20] 按输入顺序单次扫描；每次调用都是独立逻辑历史区块。"""
    ordered = tuple(entries)
    if not ordered:
        return AnnotatedHistory(text="", entries=(), fragments=(), markers=())

    entry_ids = [entry.entry_id for entry in ordered]
    if len(entry_ids) != len(set(entry_ids)):
        return AnnotatedHistory(text="", entries=ordered, fragments=(), markers=())

    text_parts: list[str] = []
    fragments: list[AnnotatedFragment] = []
    markers: list[TemporalMarker] = []
    cursor = 0
    previous: TemporalLogEntry | None = None
    last_marker_entry: TemporalLogEntry | None = None

    def append_fragment(
        *,
        fragment_id: str,
        kind: AnnotatedFragmentKind,
        entry: TemporalLogEntry,
        content: str,
        sources: tuple[SourceRef, ...],
        trust: TrustLevel,
    ) -> None:
        nonlocal cursor
        if not content:
            return
        start = cursor
        text_parts.append(content)
        cursor += len(content)
        fragments.append(
            AnnotatedFragment(
                fragment_id=fragment_id,
                kind=kind,
                entry_id=entry.entry_id,
                start=start,
                end=cursor,
                content=content,
                sources=sources,
                trust=trust,
            )
        )

    for index, entry in enumerate(ordered):
        if index:
            append_fragment(
                fragment_id=f"{entry.entry_id}:entry-separator",
                kind=AnnotatedFragmentKind.SEPARATOR,
                entry=entry,
                content=separator,
                sources=entry.sources,
                trust=entry.trust,
            )

        reasons: set[TemporalMarkerReason] = set()
        if previous is None:
            reasons.add(TemporalMarkerReason.FIRST)
        else:
            current_start = _utc(entry.span.start)
            previous_start = _utc(previous.span.start)
            previous_end = _utc(previous.span.end)
            effective_gap = max(current_start - previous_end, timedelta(0))
            if effective_gap >= policy.long_gap:
                reasons.add(TemporalMarkerReason.LONG_GAP)
            if policy.split_on_local_date_change and (
                previous.span.end.astimezone(policy.display_timezone).date()
                != entry.span.start.astimezone(policy.display_timezone).date()
            ):
                reasons.add(TemporalMarkerReason.LOCAL_DATE_CHANGE)
            if current_start < previous_start:
                reasons.add(TemporalMarkerReason.TIME_REVERSAL)
            if last_marker_entry is not None and (
                current_start - _utc(last_marker_entry.span.start) >= policy.continuous_refresh
            ):
                reasons.add(TemporalMarkerReason.REFRESH)

        if reasons:
            marker_sources = _marker_sources(policy, entry)
            marker = TemporalMarker(
                before_entry_id=entry.entry_id,
                text=format_temporal_marker(entry.span, policy),
                reasons=frozenset(reasons),
                span=entry.span,
                sources=marker_sources,
                trust=TrustLevel.UNTRUSTED_DATA,
            )
            markers.append(marker)
            append_fragment(
                fragment_id=f"{entry.entry_id}:marker",
                kind=AnnotatedFragmentKind.MARKER,
                entry=entry,
                content=marker.text,
                sources=marker.sources,
                trust=marker.trust,
            )
            if entry.body:
                append_fragment(
                    fragment_id=f"{entry.entry_id}:marker-separator",
                    kind=AnnotatedFragmentKind.SEPARATOR,
                    entry=entry,
                    content="\n",
                    sources=marker.sources,
                    trust=TrustLevel.UNTRUSTED_DATA,
                )
            last_marker_entry = entry

        append_fragment(
            fragment_id=f"{entry.entry_id}:body",
            kind=AnnotatedFragmentKind.BODY,
            entry=entry,
            content=entry.body,
            sources=entry.sources,
            trust=entry.trust,
        )
        previous = entry

    return AnnotatedHistory(
        text="".join(text_parts),
        entries=ordered,
        fragments=tuple(fragments),
        markers=tuple(markers),
    )
