"""[2026-07-19] 只有完整轮次即将越过窗口时才形成最旧连续整理批次。"""

from pathlib import Path

from bai_agent.domain.models import Role
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.curation import CurationPolicy


REVISION = "sha256:" + "1" * 64


def records(tmp_path: Path, turns: int):
    archive = RawRecordArchive(tmp_path)
    for index in range(turns):
        turn = f"turn-00000000-0000-4000-8000-{index:012d}"
        for role in (Role.USER, Role.ASSISTANT):
            archive.append(role=role, content=f"{role.value}-{index}", turn_id=turn, state_id="default", config_revision=REVISION)
    return archive.read_all()


def test_threshold_before_boundary_has_zero_batch(tmp_path: Path) -> None:
    policy = CurationPolicy(max_records=8, reserved_records=2, min_batch_records=2, max_batch_records=4)
    assert policy.next_batch(records(tmp_path, 3), curated_through=0, config_revision=REVISION) is None


def test_oldest_complete_turns_form_bounded_stable_batch(tmp_path: Path) -> None:
    policy = CurationPolicy(max_records=6, reserved_records=2, min_batch_records=2, max_batch_records=4)
    all_records = records(tmp_path, 5)
    batch = policy.next_batch(all_records, curated_through=0, config_revision=REVISION)
    assert batch is not None
    assert batch.record_ids == tuple(item.record_id for item in all_records[:4])
    assert batch.old_frontier == 0
    assert batch.new_frontier == 4
    again = policy.next_batch(all_records, curated_through=0, config_revision=REVISION)
    assert again.batch_id == batch.batch_id


def test_commit_decision_only_advances_after_success() -> None:
    assert CurationPolicy.committed_frontier(4, proposed=8, committed=False) == 4
    assert CurationPolicy.committed_frontier(4, proposed=8, committed=True) == 8
