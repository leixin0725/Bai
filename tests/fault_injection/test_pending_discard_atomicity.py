"""[2026-07-20] pending 截尾在任一原子替换故障点只暴露完整旧状态或完整新状态。"""

from pathlib import Path

import pytest

from bai_agent.domain.models import Role
from bai_agent.memory.archive import RawRecordArchive
from tests.fakes import FailureInjector


REVISION = "sha256:" + "8" * 64


def _archive_with_pending(root: Path, *, rollover: bool, hook=None) -> tuple[RawRecordArchive, str]:
    archive = RawRecordArchive(
        root, segment_max_records=2 if rollover else 8,
        segment_max_bytes=8192, max_record_bytes=2048,
    )
    turn = "turn-00000000-0000-4000-8000-000000000001"
    archive.append(role=Role.USER, content="完整输入", turn_id=turn, state_id="default", config_revision=REVISION)
    archive.append(role=Role.ASSISTANT, content="完整输出", turn_id=turn, state_id="default", config_revision=REVISION)
    pending_turn = "turn-00000000-0000-4000-8000-000000000002"
    archive.append(role=Role.USER, content="故障注入 pending", turn_id=pending_turn, state_id="default", config_revision=REVISION)
    archive.failure_hook = hook
    return archive, pending_turn


@pytest.mark.parametrize("rollover", [False, True])
@pytest.mark.parametrize("point", ["temp_created", "written", "flushed", "fsynced", "before_replace"])
def test_failure_before_replace_preserves_complete_pending(
    tmp_path: Path, rollover: bool, point: str
) -> None:
    injector = FailureInjector(point)
    archive, pending_turn = _archive_with_pending(tmp_path, rollover=rollover, hook=injector.hit)
    with pytest.raises(OSError):
        archive.discard_pending_tail(expected_turn_id=pending_turn)
    recovered = RawRecordArchive(tmp_path).read_all()
    assert [item.role for item in recovered] == [Role.USER, Role.ASSISTANT, Role.USER]
    assert recovered[-1].turn_id == pending_turn


@pytest.mark.parametrize("rollover", [False, True])
def test_failure_after_replace_preserves_complete_discarded_state(
    tmp_path: Path, rollover: bool
) -> None:
    injector = FailureInjector("after_replace")
    archive, pending_turn = _archive_with_pending(tmp_path, rollover=rollover, hook=injector.hit)
    with pytest.raises(OSError):
        archive.discard_pending_tail(expected_turn_id=pending_turn)
    recovered = RawRecordArchive(tmp_path).read_all()
    assert [item.role for item in recovered] == [Role.USER, Role.ASSISTANT]
    assert RawRecordArchive(tmp_path).discard_pending_tail() is None
