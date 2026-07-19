"""[2026-07-19] 原始归档测试永久序列、轮次约束、正文完整性与分段滚动。"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import Role
from bai_agent.memory.archive import RawRecordArchive


REVISION = "sha256:" + "1" * 64


def archive_at(path: Path, max_records: int = 3) -> RawRecordArchive:
    return RawRecordArchive(path, segment_max_records=max_records, segment_max_bytes=8192, max_record_bytes=2048)


def test_archive_rolls_segments_and_restores_global_order(tmp_path: Path) -> None:
    archive = archive_at(tmp_path)
    for index in range(7):
        archive.append(
            role=Role.USER if index % 2 == 0 else Role.ASSISTANT,
            content=f"第 {index} 条\n多行",
            turn_id=f"turn-00000000-0000-4000-8000-{index:012d}",
            state_id="default",
            config_revision=REVISION,
            created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
        )
    restored = archive_at(tmp_path).read_all()
    assert [item.global_sequence for item in restored] == list(range(1, 8))
    assert len(list((tmp_path / "raw").glob("*.jsonl"))) == 3
    assert restored[0].content == "第 0 条\n多行"


@given(st.text(min_size=1, max_size=120).filter(lambda value: not value.isspace()))
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_unicode_content_checksum_round_trip(tmp_path: Path, content: str) -> None:
    from uuid import uuid4

    archive_root = tmp_path / str(uuid4())
    archive = archive_at(archive_root, max_records=10)
    record = archive.append(
        role=Role.USER,
        content=content,
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default",
        config_revision=REVISION,
    )
    assert archive_at(archive_root, 10).read_all()[0] == record


def test_duplicate_role_in_turn_is_rejected(tmp_path: Path) -> None:
    archive = archive_at(tmp_path)
    values = dict(
        role=Role.USER,
        content="内容",
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default",
        config_revision=REVISION,
    )
    archive.append(**values)
    with pytest.raises(BaiError, match="轮次"):
        archive.append(**values)
