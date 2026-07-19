"""[2026-07-19] 每个原子写故障点只能留下完整旧版本或完整新版本。"""

from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.recovery import atomic_write
from tests.fakes import FailureInjector


@pytest.mark.parametrize("point", ["temp_created", "written", "flushed", "fsynced", "before_replace"])
def test_failure_before_replace_preserves_old_file(tmp_path: Path, point: str) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"old\n")
    with pytest.raises(OSError):
        atomic_write(target, b"new\n", FailureInjector(point).hit)
    assert target.read_bytes() == b"old\n"


def test_failure_after_replace_recovers_complete_new_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"old\n")
    with pytest.raises(OSError):
        atomic_write(target, b"new\n", FailureInjector("after_replace").hit)
    assert target.read_bytes() == b"new\n"


def test_half_line_tail_fails_closed_without_changing_bytes(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    path = raw / "00000001.jsonl"
    path.write_bytes(b'{"partial":true}')
    before = path.read_bytes()
    with pytest.raises(BaiError) as raised:
        RawRecordArchive(tmp_path).read_all()
    assert raised.value.code == "RAW_SEGMENT_INVALID"
    assert path.read_bytes() == before

