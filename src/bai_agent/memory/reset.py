"""[2026-07-19] 记忆重置在单写者锁内协调长期 YAML 与永久原始归档。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from bai_agent.domain.errors import BaiError
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.long_term import LongTermStore
from bai_agent.memory.recovery import WriterLease


@dataclass(frozen=True, slots=True)
class MemoryResetReport:
    scope: str
    raw_records_before: int
    raw_records_after: int
    long_term_items_before: int
    long_term_items_after: int
    coverage_spans_before: int
    coverage_spans_after: int
    curated_through_sequence: int

    def as_dict(self) -> dict[str, str | int]:
        return asdict(self)


class MemoryResetService:
    def __init__(
        self,
        memory_root: Path,
        *,
        segment_max_records: int = 256,
        segment_max_bytes: int = 1_048_576,
        max_record_bytes: int = 262_144,
        max_document_bytes: int = 8_388_608,
        max_items: int = 10_000,
        max_overview_chars: int = 12_000,
        writer_lock_timeout_seconds: float = 0,
    ) -> None:
        self.memory_root = memory_root
        self.archive_options = {
            "segment_max_records": segment_max_records,
            "segment_max_bytes": segment_max_bytes,
            "max_record_bytes": max_record_bytes,
        }
        self.store_options = {
            "max_document_bytes": max_document_bytes,
            "max_items": max_items,
            "max_overview_chars": max_overview_chars,
        }
        self.writer_lock_timeout_seconds = writer_lock_timeout_seconds

    def reset(self, scope: str) -> MemoryResetReport:
        if scope not in {"all", "long-term"}:
            raise BaiError("MEMORY_RESET_SCOPE_INVALID", "记忆重置作用域无效。")
        lease = WriterLease(
            self.memory_root / ".state" / "writer.lock",
            self.writer_lock_timeout_seconds,
        )
        lease.acquire()
        try:
            archive = RawRecordArchive(self.memory_root, **self.archive_options)
            archive.validate_permissions()
            store = LongTermStore(self.memory_root, archive, **self.store_options)
            permission = store.validate_permissions()
            if permission.status.value != "private":
                raise BaiError(
                    permission.error_code or "MEMORY_PERMISSION_INVALID",
                    permission.warning or "长期记忆权限无效。",
                )

            if scope == "long-term":
                before_records_count = len(archive.read_all())
                before_document = store.initialize()
                after_document = store.clear_long_term_memories()
                before_items_count = len(before_document.memories)
                before_spans_count = len(
                    before_document.coverage_overview.coverage_spans
                )
            else:
                # [2026-07-19] 空长期修订先提交，随后逆序删段；任一中断都不会留下悬空来源。
                try:
                    before_records_count = len(archive.read_all())
                except BaiError:
                    before_records_count = archive.stored_line_count()
                try:
                    before_document = store.initialize()
                    before_items_count = len(before_document.memories)
                    before_spans_count = len(
                        before_document.coverage_overview.coverage_spans
                    )
                except BaiError:
                    before_items_count = -1
                    before_spans_count = -1
                after_document = store.reset_to_factory_state()
                archive.clear()
            after_records = archive.read_all()
            return MemoryResetReport(
                scope=scope,
                raw_records_before=before_records_count,
                raw_records_after=len(after_records),
                long_term_items_before=before_items_count,
                long_term_items_after=len(after_document.memories),
                coverage_spans_before=before_spans_count,
                coverage_spans_after=len(after_document.coverage_overview.coverage_spans),
                curated_through_sequence=after_document.curation.curated_through_sequence,
            )
        finally:
            lease.release()
