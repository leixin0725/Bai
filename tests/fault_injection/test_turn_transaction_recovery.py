"""[2026-07-20] 事务故障测试覆盖三态落盘、发布与清理边界。"""

from pathlib import Path

import pytest

from bai_agent.domain.models import RawRecord, Role, new_id, utc_now
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.transaction import PreTurnCheckpoint, TurnUnitOfWork
from tests.fakes import FailureInjector


REVISION = "sha256:" + "2" * 64


@pytest.mark.parametrize("point", ["temp_created", "written", "flushed", "fsynced", "before_replace", "after_replace"])
def test_prepared_write_interrupt_never_publishes(point: str, tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    turn_id = new_id("turn")
    user = RawRecord.create(
        record_id=new_id("rec"), global_sequence=1, turn_id=turn_id, role=Role.USER,
        content="暂存", created_at=utc_now(), state_id="default", config_revision=REVISION,
    )
    injector = FailureInjector(point)
    uow = TurnUnitOfWork(tmp_path, archive, failure_hook=injector.hit)
    with pytest.raises(OSError):
        uow.begin(PreTurnCheckpoint.capture(archive, None, "default"), user)
    assert archive.read_all() == ()


def test_recovery_discards_prepared_and_forwards_pending(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    turn_id = new_id("turn")
    user = RawRecord.create(
        record_id=new_id("rec"), global_sequence=1, turn_id=turn_id, role=Role.USER,
        content="待恢复", created_at=utc_now(), state_id="default", config_revision=REVISION,
    )
    uow = TurnUnitOfWork(tmp_path, archive)
    checkpoint = PreTurnCheckpoint.capture(archive, None, "default")
    uow.begin(checkpoint, user)
    TurnUnitOfWork(tmp_path, archive).recover()
    assert archive.read_all() == ()
    uow.begin(checkpoint, user)
    uow.pending("NETWORK_UNAVAILABLE")
    TurnUnitOfWork(tmp_path, archive).recover()
    assert [item.content for item in archive.read_all()] == ["待恢复"]
