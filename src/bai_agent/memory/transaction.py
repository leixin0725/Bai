"""[2026-07-20] 三态 journal 只暂存恢复所需数据，不保存提示、来源或认证信息。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    LongTermMemoryDocument,
    PreTurnCheckpoint,
    RawRecord,
    TransactionState,
    TurnTransactionJournal,
    canonical_json,
    content_hash,
    new_id,
)
from bai_agent.memory.recovery import atomic_write
from bai_agent.security.credentials import CredentialGuard
from bai_agent.security.permissions import PermissionStatus, ensure_private_path


class TurnUnitOfWork:
    """[2026-07-20] PREPARED 可丢弃；两个 READY 已决定，只允许幂等前滚。"""

    def __init__(
        self, memory_root: Path, archive: Any, long_term_store: Any | None = None,
        *, failure_hook=None,
    ) -> None:
        self.memory_root = memory_root
        self.archive = archive
        self.long_term_store = long_term_store
        self.state_dir = memory_root / ".state"
        self.path = self.state_dir / "turn-transaction.json"
        self.failure_hook = failure_hook
        self.guard = CredentialGuard()

    @property
    def state(self) -> str | None:
        journal = self._load(required=False)
        return journal.state.value if journal is not None else None

    def _load(self, *, required: bool) -> TurnTransactionJournal | None:
        if not self.path.exists():
            if required:
                raise BaiError("TURN_TRANSACTION_MISSING", "当前轮次事务不存在。")
            return None
        try:
            directory_permission = ensure_private_path(self.state_dir, is_directory=True)
            file_permission = ensure_private_path(self.path, is_directory=False)
            if any(
                item.status != PermissionStatus.PRIVATE
                for item in (directory_permission, file_permission)
            ):
                raise BaiError("TURN_TRANSACTION_PERMISSION_INVALID", "轮次事务权限无法确认为私有。")
            payload = self.path.read_text(encoding="utf-8")
            self.guard.ensure_safe(payload)
            journal = TurnTransactionJournal.model_validate_json(payload)
        except (OSError, ValidationError, ValueError, BaiError) as exc:
            raise BaiError("TURN_TRANSACTION_INVALID", "轮次事务损坏或包含禁区数据；已阻止新轮次。") from exc
        return journal

    def _write(self, journal: TurnTransactionJournal) -> None:
        payload = (canonical_json(journal.model_dump(mode="json", exclude_none=True)) + "\n").encode("utf-8")
        self.guard.ensure_safe(payload.decode("utf-8"))
        atomic_write(self.path, payload, self.failure_hook)
        permissions = (
            ensure_private_path(self.state_dir, is_directory=True),
            ensure_private_path(self.path, is_directory=False),
        )
        if any(item.status != PermissionStatus.PRIVATE for item in permissions):
            raise BaiError("TURN_TRANSACTION_PERMISSION_INVALID", "轮次事务权限无法确认为私有。")

    def begin(self, checkpoint: PreTurnCheckpoint, provisional_user_record: RawRecord) -> None:
        if self._load(required=False) is not None:
            raise BaiError("TURN_TRANSACTION_ACTIVE", "已有轮次事务尚未收敛。")
        if provisional_user_record.role.value != "user":
            raise BaiError("TURN_TRANSACTION_INVALID", "事务暂存记录必须是 USER。")
        self._write(
            TurnTransactionJournal(
                state=TransactionState.PREPARED,
                transaction_id=new_id("tx"),
                turn_id=provisional_user_record.turn_id,
                checkpoint=checkpoint,
                provisional_user_record=provisional_user_record,
            )
        )

    def discard(self) -> None:
        journal = self._load(required=True)
        if journal.state != TransactionState.PREPARED:
            raise BaiError("TURN_TRANSACTION_STATE", "已决定的 READY 事务不能回滚。")
        self.path.unlink()

    def pending(self, failure_code: str) -> None:
        journal = self._load(required=True)
        if journal.state != TransactionState.PREPARED:
            raise BaiError("TURN_TRANSACTION_STATE", "只有 PREPARED 可转为 READY_PENDING。")
        if not failure_code or any(character.isspace() for character in failure_code):
            raise BaiError("TURN_TRANSACTION_INVALID", "pending 失败码必须是脱敏枚举。")
        self._write(
            journal.model_copy(
                update={"state": TransactionState.READY_PENDING, "pending_failure_code": failure_code}
            )
        )

    def ready(self, assistant_record: RawRecord, target_long_term_document: LongTermMemoryDocument | None = None) -> None:
        journal = self._load(required=True)
        if journal.state != TransactionState.PREPARED or assistant_record.turn_id != journal.turn_id:
            raise BaiError("TURN_TRANSACTION_STATE", "完整轮次结果与 PREPARED 不匹配。")
        target = target_long_term_document.model_dump(mode="json") if target_long_term_document else None
        self._write(
            journal.model_copy(
                update={
                    "state": TransactionState.READY_TO_COMMIT,
                    "assistant_record": assistant_record,
                    "target_long_term_document": target,
                    "target_long_term_sha256": content_hash(canonical_json(target)) if target is not None else None,
                }
            )
        )

    def commit(self) -> None:
        journal = self._load(required=False)
        if journal is None:
            return
        if journal.state == TransactionState.PREPARED:
            raise BaiError("TURN_TRANSACTION_STATE", "PREPARED 尚无可发布决定。")
        if journal.state == TransactionState.READY_PENDING:
            self.archive.append_pending_user(journal.provisional_user_record, journal.checkpoint.raw_sha256)
        else:
            assert journal.assistant_record is not None
            self.archive.append_complete_turn(
                journal.provisional_user_record,
                journal.assistant_record,
                journal.checkpoint.raw_sha256,
            )
            if journal.target_long_term_document is not None:
                if self.long_term_store is None:
                    raise BaiError("TURN_TRANSACTION_INVALID", "缺少长期记忆发布端口。")
                target = LongTermMemoryDocument.model_validate(journal.target_long_term_document)
                self.long_term_store.publish_target(
                    baseline_revision=journal.checkpoint.long_term_revision,
                    baseline_sha256=journal.checkpoint.long_term_sha256,
                    target=target,
                    target_sha256=journal.target_long_term_sha256,
                )
        self.path.unlink()

    def recover(self) -> None:
        journal = self._load(required=False)
        if journal is None:
            return
        if journal.state == TransactionState.PREPARED:
            self.path.unlink()
            return
        self.commit()


__all__ = ["PreTurnCheckpoint", "TurnUnitOfWork"]
