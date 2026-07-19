"""[2026-07-19] 长期 YAML 将记忆、来源、覆盖概览和前沿作为单一原子事实来源。"""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CreatedBy,
    LongTermMemoryDocument,
    LongTermMemoryItem,
    MemoryCoverageOverview,
    MemoryKind,
    MemoryStatus,
    SourceReference,
    SourceRelation,
    content_hash,
    new_id,
    utc_now,
)
from bai_agent.memory.recovery import atomic_write, find_temporary_files
from bai_agent.security.credentials import CredentialGuard
from bai_agent.security.permissions import PermissionResult, PermissionStatus, ensure_private_path


class LongTermStore:
    def __init__(
        self,
        memory_root: Path,
        archive,
        *,
        max_document_bytes: int = 8_388_608,
        max_items: int = 10_000,
        max_overview_chars: int = 12_000,
        failure_hook=None,
    ) -> None:
        self.memory_root = memory_root
        self.archive = archive
        self.path = memory_root / "long_term.yaml"
        self.state_dir = memory_root / ".state"
        self.last_valid_path = self.state_dir / "long_term.last-valid.yaml"
        self.max_document_bytes = max_document_bytes
        self.max_items = max_items
        self.max_overview_chars = max_overview_chars
        self.failure_hook = failure_hook
        self.guard = CredentialGuard()
        self.read_only = False
        self.loaded_hash: str | None = None
        self.yaml = YAML()
        self.yaml.preserve_quotes = True
        self.yaml.width = 1000
        self.memory_root.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        ensure_private_path(self.memory_root, is_directory=True)
        ensure_private_path(self.state_dir, is_directory=True)

    def _render(
        self,
        document: LongTermMemoryDocument,
        *,
        preserve_existing_layout: bool = True,
    ) -> bytes:
        values = document.model_dump(mode="json")
        root: Any = None
        if preserve_existing_layout and self.path.exists():
            try:
                root = self.yaml.load(self.path.read_text(encoding="utf-8"))
            except Exception:
                root = None
        if not isinstance(root, dict):
            root = {}
        for key in ("schema_version", "revision", "curation", "coverage_overview", "memories"):
            root[key] = values[key]
        output = StringIO()
        self.yaml.dump(root, output)
        payload = output.getvalue().replace("\r\n", "\n").encode("utf-8")
        if len(payload) > self.max_document_bytes:
            raise BaiError("MEMORY_DOCUMENT_TOO_LARGE", "长期记忆文档超过配置大小上限。")
        return payload

    def _parse_bytes(self, payload: bytes) -> LongTermMemoryDocument:
        if len(payload) > self.max_document_bytes:
            raise BaiError("MEMORY_DOCUMENT_TOO_LARGE", "长期记忆文档超过配置大小上限。")
        try:
            data = self.yaml.load(payload.decode("utf-8"))
            document = LongTermMemoryDocument.model_validate(data)
        except (UnicodeDecodeError, ValidationError, Exception) as exc:
            if isinstance(exc, BaiError):
                raise
            raise BaiError("MEMORY_DOCUMENT_INVALID", "长期记忆文件未通过校验；已保留原文件。") from exc
        if len(document.memories) > self.max_items:
            raise BaiError("MEMORY_DOCUMENT_TOO_LARGE", "长期记忆条目超过配置上限。")
        if len(document.coverage_overview.text) > self.max_overview_chars:
            raise BaiError("MEMORY_OVERVIEW_TOO_LARGE", "记忆覆盖概览超过配置上限。")
        self._validate_sources(document)
        return document

    def _validate_sources(self, document: LongTermMemoryDocument) -> None:
        if not document.memories and not document.coverage_overview.coverage_spans:
            return
        raw = {item.record_id: item for item in self.archive.read_all()}
        for memory in document.memories:
            for source in memory.source_refs:
                record = raw.get(source.record_id)
                if record is None:
                    raise BaiError("SOURCE_RECORD_MISSING", "长期记忆来源记录不存在。")
                if record.content_sha256 != source.record_sha256:
                    raise BaiError("SOURCE_HASH_MISMATCH", "长期记忆来源摘要不匹配。")
        for span in document.coverage_overview.coverage_spans:
            expected = list(range(span.start_sequence, span.end_sequence + 1))
            actual_records = [raw.get(record_id) for record_id in span.record_ids]
            if any(item is None for item in actual_records):
                raise BaiError("SOURCE_RECORD_MISSING", "覆盖概览引用的原始记录不存在。")
            if [item.global_sequence for item in actual_records if item] != expected:
                raise BaiError("MEMORY_COVERAGE_INVALID", "覆盖概览的序号与记录不一致。")
            if tuple(item.content_sha256 for item in actual_records if item) != span.record_hashes:
                raise BaiError("SOURCE_HASH_MISMATCH", "覆盖概览的记录摘要不匹配。")

    def _refresh_last_valid(self, payload: bytes) -> None:
        # [2026-07-19] 内容未变时保留现有恢复点，避免每次启动产生无意义原子写与权限调整。
        if self.last_valid_path.exists() and self.last_valid_path.read_bytes() == payload:
            return
        atomic_write(self.last_valid_path, payload)
        ensure_private_path(self.last_valid_path, is_directory=False)

    def initialize(self) -> LongTermMemoryDocument:
        if not self.path.exists():
            payload = self._render(LongTermMemoryDocument.empty())
            atomic_write(self.path, payload)
            ensure_private_path(self.path, is_directory=False)
        return self.load()

    def load(self) -> LongTermMemoryDocument:
        try:
            payload = self.path.read_bytes()
            document = self._parse_bytes(payload)
        except (OSError, BaiError) as primary_error:
            if not self.last_valid_path.exists():
                if isinstance(primary_error, BaiError):
                    raise
                raise BaiError("MEMORY_DOCUMENT_INVALID", "长期记忆文件不存在或不可读。") from primary_error
            fallback = self._parse_bytes(self.last_valid_path.read_bytes())
            self.read_only = True
            self.loaded_hash = None
            return fallback

        # [2026-07-19] 只有长期记忆语义发生外部变化时才标记 manual；纯注释编辑原样保留。
        if self.last_valid_path.exists():
            try:
                previous_payload = self.last_valid_path.read_bytes()
                previous = self._parse_bytes(previous_payload) if previous_payload != payload else None
                if previous is not None and document.memories != previous.memories:
                    if document.curation != previous.curation or document.coverage_overview.coverage_spans != previous.coverage_overview.coverage_spans:
                        raise BaiError("MEMORY_DOCUMENT_INVALID", "人工维护不能直接修改整理前沿或覆盖区间。")
                    old = {item.memory_id: item for item in previous.memories}
                    memories = []
                    for item in document.memories:
                        if old.get(item.memory_id) != item:
                            item = item.model_copy(update={"created_by": CreatedBy.MANUAL, "updated_at": utc_now()})
                        memories.append(item)
                    next_revision = previous.revision + 1
                    document = LongTermMemoryDocument(
                        schema_version=1,
                        revision=next_revision,
                        curation=previous.curation,
                        coverage_overview=document.coverage_overview.model_copy(update={"revision": next_revision}),
                        memories=tuple(memories),
                    )
                    self.loaded_hash = content_hash(payload)
                    self.read_only = False
                    return self.commit(document)
            except BaiError:
                raise
            except Exception:
                pass
        self.read_only = False
        self.loaded_hash = content_hash(payload)
        self._refresh_last_valid(payload)
        return document

    def commit(
        self,
        document: LongTermMemoryDocument,
        *,
        preserve_existing_layout: bool = True,
    ) -> LongTermMemoryDocument:
        if self.read_only:
            raise BaiError("MEMORY_READ_ONLY_FALLBACK", "长期记忆主文件无效，当前只读回退禁止写入。")
        try:
            validated = LongTermMemoryDocument.model_validate(document.model_dump(mode="python"))
        except ValidationError as exc:
            raise BaiError("MEMORY_DOCUMENT_INVALID", "长期记忆联合修订无效。") from exc
        self._validate_sources(validated)
        for item in validated.memories:
            self.guard.ensure_safe(item.text)
        self.guard.ensure_safe(validated.coverage_overview.text)
        if len(validated.coverage_overview.text) > self.max_overview_chars:
            raise BaiError("MEMORY_OVERVIEW_TOO_LARGE", "记忆覆盖概览超过配置上限。")
        if self.path.exists() and self.loaded_hash is not None:
            current = self.path.read_bytes()
            if content_hash(current) != self.loaded_hash:
                raise BaiError("CONCURRENT_MANUAL_EDIT", "检测到并发人工修改；提交已中止。", retryable=True)
        payload = self._render(
            validated,
            preserve_existing_layout=preserve_existing_layout,
        )
        atomic_write(self.path, payload, self.failure_hook)
        ensure_private_path(self.path, is_directory=False)
        self.loaded_hash = content_hash(payload)
        self._refresh_last_valid(payload)
        return validated

    def initialize_with_manual_memory(self, text: str, records: tuple) -> LongTermMemoryDocument:
        safe_text = self.guard.ensure_safe(text)
        if not records:
            raise BaiError("SOURCE_RECORD_MISSING", "人工长期记忆必须引用至少一条原始记录。")
        document = self.initialize()
        now = utc_now()
        item = LongTermMemoryItem(
            memory_id=new_id("mem"),
            kind=MemoryKind.FACT,
            text=safe_text,
            status=MemoryStatus.ACTIVE,
            source_refs=tuple(
                SourceReference(
                    record_id=record.record_id,
                    relation=SourceRelation.MANUAL_BASIS,
                    record_sha256=record.content_sha256,
                )
                for record in records
            ),
            created_by=CreatedBy.MANUAL,
            created_at=now,
            updated_at=now,
            supersedes=(),
            tags=(),
        )
        revision = document.revision + 1
        updated = LongTermMemoryDocument(
            schema_version=1,
            revision=revision,
            curation=document.curation,
            coverage_overview=document.coverage_overview.model_copy(update={"revision": revision}),
            memories=(*document.memories, item),
        )
        return self.commit(updated)

    def clear_long_term_memories(self) -> LongTermMemoryDocument:
        """[2026-07-19] 清空派生正文但保留覆盖索引，避免旧原文在下一轮被立即重新整理。"""
        document = self.initialize()
        revision = document.revision + 1
        updated = LongTermMemoryDocument(
            schema_version=1,
            revision=revision,
            curation=document.curation,
            coverage_overview=MemoryCoverageOverview(
                revision=revision,
                text="长期记忆已清空；既有已整理范围仅保留来源覆盖索引。",
                coverage_spans=document.coverage_overview.coverage_spans,
            ),
            memories=(),
        )
        committed = self.commit(updated, preserve_existing_layout=False)
        self._clear_recovery_temporary_files()
        return committed

    def reset_to_factory_state(self) -> LongTermMemoryDocument:
        """[2026-07-19] 全量重置先原子落盘空文档，再由归档层逆序清除原始段。"""
        # [2026-07-19] 显式出厂重置允许覆盖无效主文件/恢复副本，同时用当前摘要防并发改写。
        self.read_only = False
        self.loaded_hash = content_hash(self.path.read_bytes()) if self.path.exists() else None
        committed = self.commit(
            LongTermMemoryDocument.empty(),
            preserve_existing_layout=False,
        )
        self._clear_recovery_temporary_files()
        return committed

    def _clear_recovery_temporary_files(self) -> None:
        # [2026-07-19] 重置后的恢复副本不能继续保留旧长期正文或 YAML 注释。
        for directory in (self.memory_root, self.state_dir):
            for path in find_temporary_files(directory):
                path.unlink()

    def validate_permissions(self) -> PermissionResult:
        results = [
            ensure_private_path(self.memory_root, is_directory=True),
            ensure_private_path(self.state_dir, is_directory=True),
        ]
        if self.path.exists():
            results.append(ensure_private_path(self.path, is_directory=False))
        if self.last_valid_path.exists():
            results.append(ensure_private_path(self.last_valid_path, is_directory=False))
        for result in results:
            if result.status != PermissionStatus.PRIVATE:
                return result
        return PermissionResult(PermissionStatus.PRIVATE)
