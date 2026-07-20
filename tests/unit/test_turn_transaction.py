"""[2026-07-20] 三态轮次事务测试区分拒绝、普通失败和完整提交。"""

from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import RawRecord, Role, content_hash, new_id, utc_now
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.transaction import PreTurnCheckpoint, TurnUnitOfWork


REVISION = "sha256:" + "1" * 64


def record(role: Role, turn_id: str, content: str, sequence: int) -> RawRecord:
    return RawRecord.create(
        record_id=new_id("rec"), global_sequence=sequence, turn_id=turn_id, role=role,
        content=content, created_at=utc_now(), state_id="default", config_revision=REVISION,
    )


def test_discard_pending_and_complete_paths_are_distinct(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    turn_id = new_id("turn")
    user = record(Role.USER, turn_id, "用户", 1)
    uow = TurnUnitOfWork(tmp_path, archive)
    uow.begin(PreTurnCheckpoint.capture(archive, None, "default"), user)
    assert uow.state == "PREPARED"
    uow.discard()
    assert uow.state is None and archive.read_all() == ()

    uow.begin(PreTurnCheckpoint.capture(archive, None, "default"), user)
    uow.pending("PROVIDER_UNAVAILABLE")
    uow.commit()
    uow.commit()
    assert len(archive.read_all()) == 1


def test_illegal_transition_and_baseline_conflict_fail_closed(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    uow = TurnUnitOfWork(tmp_path, archive)
    with pytest.raises(BaiError):
        uow.pending("PROVIDER_UNAVAILABLE")
