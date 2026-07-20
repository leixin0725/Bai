"""[2026-07-20] 长期记忆时间只从单次已验证 raw 快照动态投影，不建立第二事实源。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from pydantic import ValidationError

from bai_agent.domain.errors import BaiError, TemporalEntryError
from bai_agent.domain.models import (
    LongTermMemoryItem,
    MemoryCoverageOverview,
    RawRecord,
    SourceKind,
    SourceRef,
    TemporalLogEntry,
    TemporalSpan,
    TemporalTimeKind,
    TrustLevel,
    canonical_json,
    content_hash,
)


@dataclass(frozen=True, slots=True)
class MemoryTemporalProjector:
    """[2026-07-20] 一个 projector 对应一个 immutable raw index，禁止逐记忆重读 archive。"""

    raw_records: tuple[RawRecord, ...]
    raw_by_id: Mapping[str, RawRecord]
    raw_revision: str

    @classmethod
    def from_records(cls, records) -> "MemoryTemporalProjector":
        ordered = tuple(records)
        index = {record.record_id: record for record in ordered}
        if len(index) != len(ordered):
            raise TemporalEntryError("原始记录快照包含重复 record id，模型调用已阻止。")
        revision = content_hash(
            canonical_json(
                [
                    {
                        "record_id": record.record_id,
                        "content_sha256": record.content_sha256,
                        "global_sequence": record.global_sequence,
                    }
                    for record in ordered
                ]
            )
        )
        return cls(ordered, MappingProxyType(index), revision)

    def _raw_source(self, record: RawRecord) -> SourceRef:
        return SourceRef(
            source_kind=SourceKind.DATA_FILE,
            source_id=f"raw:{record.record_id}",
            project_relative_path="data/memory/raw",
            content_sha256=record.content_sha256,
            revision=self.raw_revision,
            entity_ids=(record.record_id,),
            producer="memory_temporal_projector",
        )

    @staticmethod
    def _memory_source(item: LongTermMemoryItem) -> SourceRef:
        return SourceRef(
            source_kind=SourceKind.DATA_FILE,
            source_id=f"memory:{item.memory_id}",
            project_relative_path="data/memory/long_term.yaml",
            content_sha256=content_hash(item.text),
            entity_ids=(item.memory_id, *(source.record_id for source in item.source_refs)),
            producer="memory_temporal_projector",
        )

    def _span(self, *, start, end, kind: TemporalTimeKind) -> TemporalSpan:
        try:
            return TemporalSpan(start=start, end=end, kind=kind)
        except ValidationError as exc:
            raise TemporalEntryError("来源记录时间无效，模型调用已阻止。") from exc

    def _resolve_memory_records(self, item: LongTermMemoryItem) -> tuple[RawRecord, ...]:
        resolved: list[RawRecord] = []
        seen: set[str] = set()
        for reference in item.source_refs:
            record = self.raw_by_id.get(reference.record_id)
            if record is None:
                raise BaiError("SOURCE_RECORD_MISSING", "长期记忆来源记录不存在。")
            if record.content_sha256 != reference.record_sha256:
                raise BaiError("SOURCE_HASH_MISMATCH", "长期记忆来源摘要不匹配。")
            if record.record_id not in seen:
                seen.add(record.record_id)
                resolved.append(record)
        if not resolved:
            raise BaiError("SOURCE_RECORD_MISSING", "长期记忆没有可验证来源记录。")
        return tuple(resolved)

    def project_raw(self, record: RawRecord, *, body: str | None = None) -> TemporalLogEntry:
        snapshot_record = self.raw_by_id.get(record.record_id)
        if snapshot_record is None:
            raise BaiError("SOURCE_RECORD_MISSING", "原始记录不属于当前已验证快照。")
        if snapshot_record.content_sha256 != record.content_sha256:
            raise BaiError("SOURCE_HASH_MISMATCH", "原始记录摘要与当前快照不一致。")
        return TemporalLogEntry(
            entry_id=record.record_id,
            body=record.content if body is None else body,
            span=self._span(
                start=record.created_at,
                end=record.created_at,
                kind=TemporalTimeKind.EVENT,
            ),
            sources=(self._raw_source(record),),
            trust=TrustLevel.UNTRUSTED_DATA,
            metadata={"record_id": record.record_id, "role": record.role.value},
        )

    def project_memory(
        self,
        item: LongTermMemoryItem,
        *,
        body: str | None = None,
        memory_source: SourceRef | None = None,
    ) -> TemporalLogEntry:
        if not isinstance(item, LongTermMemoryItem):
            raise BaiError("MEMORY_DOCUMENT_INVALID", "仅支持已验证的长期记忆 schema v1 条目。")
        records = self._resolve_memory_records(item)
        instants = [record.created_at for record in records]
        sources = (memory_source or self._memory_source(item), *(self._raw_source(record) for record in records))
        return TemporalLogEntry(
            entry_id=item.memory_id,
            body=item.text if body is None else body,
            span=self._span(
                start=min(instants),
                end=max(instants),
                kind=TemporalTimeKind.SOURCE_RANGE,
            ),
            sources=sources,
            trust=TrustLevel.UNTRUSTED_DATA,
            metadata={"memory_id": item.memory_id},
        )

    def project_overview(
        self,
        overview: MemoryCoverageOverview,
        *,
        body: str | None = None,
        overview_source: SourceRef | None = None,
    ) -> TemporalLogEntry | None:
        if not overview.coverage_spans:
            return None
        records: list[RawRecord] = []
        seen: set[str] = set()
        for span in overview.coverage_spans:
            for sequence, record_id, digest in zip(
                range(span.start_sequence, span.end_sequence + 1),
                span.record_ids,
                span.record_hashes,
                strict=True,
            ):
                record = self.raw_by_id.get(record_id)
                if record is None:
                    raise BaiError("SOURCE_RECORD_MISSING", "覆盖概览引用的原始记录不存在。")
                if record.content_sha256 != digest:
                    raise BaiError("SOURCE_HASH_MISMATCH", "覆盖概览的记录摘要不匹配。")
                if record.global_sequence != sequence:
                    raise BaiError("MEMORY_COVERAGE_INVALID", "覆盖概览序号与原始记录不一致。")
                if record.record_id not in seen:
                    seen.add(record.record_id)
                    records.append(record)
        if not records:
            raise BaiError("MEMORY_COVERAGE_INVALID", "覆盖概览没有可验证来源记录。")
        entity_ids = tuple(record.record_id for record in records)
        source = overview_source or SourceRef(
            source_kind=SourceKind.DATA_FILE,
            source_id=f"memory:overview:{overview.revision}",
            project_relative_path="data/memory/long_term.yaml",
            content_sha256=content_hash(overview.text),
            revision=str(overview.revision),
            entity_ids=entity_ids,
            producer="memory_temporal_projector",
        )
        instants = [record.created_at for record in records]
        return TemporalLogEntry(
            entry_id=f"overview:{overview.revision}",
            body=overview.text if body is None else body,
            span=self._span(
                start=min(instants),
                end=max(instants),
                kind=TemporalTimeKind.SOURCE_RANGE,
            ),
            sources=(source, *(self._raw_source(record) for record in records)),
            trust=TrustLevel.UNTRUSTED_DATA,
            metadata={"overview_revision": overview.revision},
        )
