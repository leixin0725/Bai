"""[2026-07-19] JSONL 分段是已确认原始聊天记录的永久、不可变语义归档。"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from pydantic import ValidationError

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import RawRecord, Role, canonical_json, new_id, utc_now
from bai_agent.memory.recovery import atomic_write
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

    def permission_results(self) -> tuple[Any, ...]:
        results = [ensure_private_path(self.memory_root, is_directory=True), ensure_private_path(self.raw_dir, is_directory=True)]
        results.extend(ensure_private_path(path, is_directory=False) for path in self._segments())
        return tuple(results)

    def validate_permissions(self) -> None:
        failed = [item for item in self.permission_results() if item.status != PermissionStatus.PRIVATE]
        if failed:
            raise BaiError(failed[0].error_code or "MEMORY_PERMISSION_INVALID", failed[0].warning or "明文记忆权限无效。")
