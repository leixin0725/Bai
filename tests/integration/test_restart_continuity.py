"""[2026-07-19] 启动次数不能形成会话边界，全部记录共用一个全局序列。"""

from pathlib import Path

from bai_agent.domain.models import Role
from bai_agent.memory.archive import RawRecordArchive


REVISION = "sha256:" + "1" * 64


def test_empty_and_one_hundred_turns_across_ten_restarts(tmp_path: Path) -> None:
    assert RawRecordArchive(tmp_path).read_all() == ()
    for restart in range(10):
        archive = RawRecordArchive(tmp_path, segment_max_records=17)
        for offset in range(10):
            index = restart * 10 + offset
            turn_id = f"turn-00000000-0000-4000-8000-{index:012d}"
            archive.append(
                role=Role.USER,
                content=f"事实-{index}",
                turn_id=turn_id,
                state_id="default",
                config_revision=REVISION,
            )
            archive.append(
                role=Role.ASSISTANT,
                content=f"确认-{index}",
                turn_id=turn_id,
                state_id="default",
                config_revision=REVISION,
            )
        assert len(RawRecordArchive(tmp_path).read_all()) == (restart + 1) * 20
    records = RawRecordArchive(tmp_path).read_all()
    assert [record.global_sequence for record in records] == list(range(1, 201))
    assert records[-1].content == "确认-99"
