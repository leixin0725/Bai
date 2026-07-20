"""[2026-07-19] JSONL 分段是已确认原始聊天记录的永久、不可变语义归档。"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from pydantic import ValidationError

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import RawRecord, Role, SourceKind, SourceRef, canonical_json, content_hash, new_id, utc_now
from bai_agent.memory.recovery import atomic_write, find_temporary_files
from bai_agent.security.credentials import CredentialGuard
from bai_agent.security.permissions import PermissionStatus, ensure_private_path


_SEGMENT = re.compile(r"^[0-9]{8}\.jsonl$")


class RawRecordArchive:
    def __init__(
        self,
        memory_root: Path,
        *,
        segment_max_records: int = 256,
        segment_max_bytes: int = 1_048_576,
        max_record_bytes: int = 262_144,
        credential_guard: CredentialGuard | None = None,
        failure_hook=None,
    ) -> None:
        self.memory_root = memory_root
        self.raw_dir = memory_root / "raw"
        self.segment_max_records = segment_max_records
        self.segment_max_bytes = segment_max_bytes
        self.max_record_bytes = max_record_bytes
        self.guard = credential_guard or CredentialGuard()
        self.failure_hook = failure_hook
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        ensure_private_path(self.raw_dir, is_directory=True)

    def _segments(self) -> list[Path]:
        return sorted(path for path in self.raw_dir.iterdir() if path.is_file() and _SEGMENT.match(path.name))

    def read_all(self) -> tuple[RawRecord, ...]:
        records: list[RawRecord] = []
        expected_sequence = 1
        seen_ids: set[str] = set()
        turn_roles: set[tuple[str, Role]] = set()
        for expected_segment, path in enumerate(self._segments(), start=1):
            if path.name != f"{expected_segment:08d}.jsonl":
                raise BaiError("RAW_SEGMENT_INVALID", "原始记录分段编号无效。")
            payload = path.read_bytes()
            if payload and not payload.endswith(b"\n"):
                raise BaiError("RAW_SEGMENT_INVALID", "原始记录尾部不是完整行。")
            for line in payload.splitlines():
                try:
                    record = RawRecord.model_validate_json(line)
                except (ValidationError, ValueError) as exc:
                    raise BaiError("RAW_SEGMENT_INVALID", "原始记录字段或摘要无效。") from exc
                if record.global_sequence != expected_sequence:
                    raise BaiError("RAW_SEQUENCE_GAP", "原始记录全局序列存在缺口。")
                key = (record.turn_id, record.role)
                if record.record_id in seen_ids or key in turn_roles:
                    raise BaiError("RAW_SEGMENT_INVALID", "原始记录 ID 或轮次角色重复。")
                seen_ids.add(record.record_id)
                turn_roles.add(key)
                records.append(record)
                expected_sequence += 1
        return tuple(records)

    def append(
        self,
        *,
        role: Role,
        content: str,
        turn_id: str,
        state_id: str,
        config_revision: str,
        record_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RawRecord:
        safe_content = self.guard.ensure_safe(content)
        records = self.read_all()
        if any(item.turn_id == turn_id and item.role == role for item in records):
            raise BaiError("TURN_ROLE_DUPLICATE", "同一轮次不能重复确认相同角色记录。")
        record = RawRecord.create(
            record_id=record_id or new_id("rec"),
            global_sequence=len(records) + 1,
            turn_id=turn_id,
            role=role,
            content=safe_content,
            created_at=created_at or utc_now(),
            state_id=state_id,
            config_revision=config_revision,
        )
        line = (canonical_json(record.model_dump(mode="json")) + "\n").encode("utf-8")
        if len(line) > self.max_record_bytes:
            raise BaiError("RAW_RECORD_TOO_LARGE", "原始记录超过配置大小上限。")

        segments = self._segments()
        if not segments:
            target = self.raw_dir / "00000001.jsonl"
            existing = b""
        else:
            target = segments[-1]
            existing = target.read_bytes()
            count = len(existing.splitlines())
            if count >= self.segment_max_records or len(existing) + len(line) > self.segment_max_bytes:
                target = self.raw_dir / f"{len(segments) + 1:08d}.jsonl"
                existing = b""
        atomic_write(target, existing + line, self.failure_hook)
        ensure_private_path(target, is_directory=False)
        return record

    def pending_turn(self) -> RawRecord | None:
        records = self.read_all()
        if records and records[-1].role == Role.USER:
            return records[-1]
        return None

    def identity_hash(self, records: tuple[RawRecord, ...] | None = None) -> str:
        values = self.read_all() if records is None else records
        return content_hash(
            canonical_json(
                [{"record_id": item.record_id, "content_sha256": item.content_sha256} for item in values]
            )
        )

    def append_pending_user(self, record: RawRecord, checkpoint_hash: str) -> RawRecord:
        """[2026-07-20] 普通失败前滚为单条 USER；重放只接受基线或同一目标。"""
        records = self.read_all()
        existing = next((item for item in records if item.record_id == record.record_id), None)
        if existing is not None:
            if existing.role != Role.USER or existing.turn_id != record.turn_id or existing.content_sha256 != record.content_sha256:
                raise BaiError("TURN_TRANSACTION_CONFLICT", "pending 目标记录身份冲突。")
            return existing
        if self.identity_hash(records) != checkpoint_hash:
            raise BaiError("TURN_TRANSACTION_CONFLICT", "raw 基线已变化，未覆盖外部修改。")
        return self.append(
            role=Role.USER,
            content=record.content,
            turn_id=record.turn_id,
            state_id=record.state_id,
            config_revision=record.config_revision,
            record_id=record.record_id,
            created_at=record.created_at,
        )

    def append_complete_turn(
        self,
        user: RawRecord,
        assistant: RawRecord,
        checkpoint_hash: str,
    ) -> tuple[RawRecord, RawRecord]:
        """[2026-07-20] 完整轮次按 USER/ASSISTANT 固定顺序幂等前滚，支持跨分段恢复。"""
        records = self.read_all()
        by_id = {item.record_id: item for item in records}
        published_user = by_id.get(user.record_id)
        if published_user is None:
            if self.identity_hash(records) != checkpoint_hash:
                raise BaiError("TURN_TRANSACTION_CONFLICT", "raw 基线已变化，未覆盖外部修改。")
            published_user = self.append(
                role=Role.USER,
                content=user.content,
                turn_id=user.turn_id,
                state_id=user.state_id,
                config_revision=user.config_revision,
                record_id=user.record_id,
                created_at=user.created_at,
            )
            records = self.read_all()
        elif published_user.role != Role.USER or published_user.content_sha256 != user.content_sha256:
            raise BaiError("TURN_TRANSACTION_CONFLICT", "USER 目标记录身份冲突。")
        by_id = {item.record_id: item for item in records}
        published_assistant = by_id.get(assistant.record_id)
        if published_assistant is None:
            tail = records[-1] if records else None
            if tail is None or tail.record_id != published_user.record_id:
                raise BaiError("TURN_TRANSACTION_CONFLICT", "ASSISTANT 发布前 raw 尾部不匹配。")
            published_assistant = self.append(
                role=Role.ASSISTANT,
                content=assistant.content,
                turn_id=assistant.turn_id,
                state_id=assistant.state_id,
                config_revision=assistant.config_revision,
                record_id=assistant.record_id,
                created_at=assistant.created_at,
            )
        elif published_assistant.role != Role.ASSISTANT or published_assistant.content_sha256 != assistant.content_sha256:
            raise BaiError("TURN_TRANSACTION_CONFLICT", "ASSISTANT 目标记录身份冲突。")
        return published_user, published_assistant

    def clear(self) -> int:
        """[2026-07-19] 从末段向前删除，故障中断时剩余记录仍保持连续前缀。"""
        segments = self._segments()
        for path in reversed(segments):
            path.unlink()
        for path in find_temporary_files(self.raw_dir):
            path.unlink()
        return len(segments)

    def stored_line_count(self) -> int:
        """[2026-07-19] 出厂重置报告可统计损坏行，但不会把它们当成有效原始记录。"""
        return sum(len(path.read_bytes().splitlines()) for path in self._segments())

    def record_index(self) -> dict[str, tuple[str, int, int, int]]:
        """[2026-07-19] offset 索引可从正式段重建，因此永远不是第二事实来源。"""
        index: dict[str, tuple[str, int, int, int]] = {}
        for path in self._segments():
            offset = 0
            for line in path.read_bytes().splitlines(keepends=True):
                try:
                    record = RawRecord.model_validate_json(line)
                except ValidationError as exc:
                    raise BaiError("RAW_SEGMENT_INVALID", "原始记录字段或摘要无效。") from exc
                index[record.record_id] = (path.name, offset, len(line), record.global_sequence)
                offset += len(line)
        return index

    def source_ref(self, record: RawRecord) -> SourceRef:
        """[2026-07-20] raw 来源使用真实分段与稳定 record id，不按正文定位。"""
        location = self.record_index().get(record.record_id)
        segment = location[0] if location else "pending-transaction"
        return SourceRef(
            source_kind=SourceKind.DATA_FILE,
            source_id=f"raw:{record.record_id}",
            project_relative_path=f"data/memory/raw/{segment}",
            content_sha256=record.content_sha256,
            revision=self.identity_hash(),
            entity_ids=(record.record_id,),
            producer="raw_record_archive",
        )

    def permission_results(self) -> tuple[Any, ...]:
        results = [ensure_private_path(self.memory_root, is_directory=True), ensure_private_path(self.raw_dir, is_directory=True)]
        results.extend(ensure_private_path(path, is_directory=False) for path in self._segments())
        return tuple(results)

    def validate_permissions(self) -> None:
        failed = [item for item in self.permission_results() if item.status != PermissionStatus.PRIVATE]
        if failed:
            raise BaiError(failed[0].error_code or "MEMORY_PERMISSION_INVALID", failed[0].warning or "明文记忆权限无效。")
