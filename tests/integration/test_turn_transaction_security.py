"""[2026-07-20] 私有 journal 对损坏、禁区字段、秘密和人工冲突一律失败关闭。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import RawRecord, Role, new_id, utc_now
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.transaction import PreTurnCheckpoint, TurnUnitOfWork


REVISION = "sha256:" + "5" * 64


def _prepared(tmp_path: Path, content: str = "事务输入") -> tuple[RawRecordArchive, TurnUnitOfWork]:
    archive = RawRecordArchive(tmp_path)
    record = RawRecord.create(
        record_id=new_id("rec"), global_sequence=1, turn_id=new_id("turn"),
        role=Role.USER, content=content, created_at=utc_now(), state_id="default",
        config_revision=REVISION,
    )
    uow = TurnUnitOfWork(tmp_path, archive)
    uow.begin(PreTurnCheckpoint.capture(archive, None, "default"), record)
    return archive, uow


def test_journal_is_private_and_schema_version_is_strict(tmp_path: Path) -> None:
    _, uow = _prepared(tmp_path)
    if os.name != "nt":
        assert uow.path.stat().st_mode & 0o777 == 0o600
        assert uow.path.parent.stat().st_mode & 0o777 == 0o700
    payload = json.loads(uow.path.read_text(encoding="utf-8"))
    payload["schema_version"] = 999
    uow.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BaiError) as caught:
        TurnUnitOfWork(tmp_path, RawRecordArchive(tmp_path)).recover()
    assert caught.value.code == "TURN_TRANSACTION_INVALID"


@pytest.mark.parametrize("field", ["prompt_payload", "provenance", "credential", "unknown_field"])
def test_journal_rejects_unknown_forbidden_fields(tmp_path: Path, field: str) -> None:
    _, uow = _prepared(tmp_path)
    payload = json.loads(uow.path.read_text(encoding="utf-8"))
    payload[field] = "不得落盘"
    uow.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(BaiError) as caught:
        uow.recover()
    assert caught.value.code == "TURN_TRANSACTION_INVALID"
    assert "不得落盘" not in caught.value.safe_message


def test_journal_rejects_credential_without_echoing_it(tmp_path: Path) -> None:
    secret = "sk-" + "x" * 32
    _, uow = _prepared(tmp_path)
    payload = json.loads(uow.path.read_text(encoding="utf-8"))
    payload["provisional_user_record"]["content"] = secret
    uow.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BaiError) as caught:
        uow.recover()
    assert caught.value.code == "TURN_TRANSACTION_INVALID"
    assert secret not in caught.value.safe_message


def test_ready_pending_manual_edit_conflict_blocks_without_overwrite(tmp_path: Path) -> None:
    archive, uow = _prepared(tmp_path, "原事务输入")
    uow.pending("NETWORK_UNAVAILABLE")
    external = archive.append(
        role=Role.USER, content="人工追加", turn_id=new_id("turn"), state_id="default",
        config_revision=REVISION,
    )
    with pytest.raises(BaiError) as caught:
        uow.recover()
    assert caught.value.code == "TURN_TRANSACTION_CONFLICT"
    assert [item.record_id for item in archive.read_all()] == [external.record_id]
    assert uow.path.exists()
    assert "原事务输入" not in caught.value.safe_message
