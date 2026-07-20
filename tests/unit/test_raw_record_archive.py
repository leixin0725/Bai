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


def _append_turn(archive: RawRecordArchive, turn_suffix: int, user: str, assistant: str) -> None:
    turn_id = f"turn-00000000-0000-4000-8000-{turn_suffix:012d}"
    archive.append(
        role=Role.USER, content=user, turn_id=turn_id, state_id="default",
        config_revision=REVISION,
    )
    archive.append(
        role=Role.ASSISTANT, content=assistant, turn_id=turn_id, state_id="default",
        config_revision=REVISION,
    )


def test_discard_pending_tail_preserves_completed_history_and_reuses_empty_segment(
    tmp_path: Path,
) -> None:
    archive = archive_at(tmp_path, max_records=2)
    _append_turn(archive, 1, "已完成输入", "已完成输出")
    pending = archive.append(
        role=Role.USER, content="待放弃输入",
        turn_id="turn-00000000-0000-4000-8000-000000000002",
        state_id="default", config_revision=REVISION,
    )

    assert archive.discard_pending_tail(expected_turn_id=pending.turn_id) == pending.turn_id
    assert [(item.role, item.content) for item in archive.read_all()] == [
        (Role.USER, "已完成输入"), (Role.ASSISTANT, "已完成输出")
    ]
    tail_segment = tmp_path / "raw" / "00000002.jsonl"
    assert tail_segment.exists() and tail_segment.read_bytes() == b""

    replacement = archive.append(
        role=Role.USER, content="新输入",
        turn_id="turn-00000000-0000-4000-8000-000000000003",
        state_id="default", config_revision=REVISION,
    )
    assert replacement.global_sequence == 3
    assert len(list((tmp_path / "raw").glob("*.jsonl"))) == 2


def test_discard_pending_tail_is_noop_when_absent(tmp_path: Path) -> None:
    archive = archive_at(tmp_path)
    assert archive.discard_pending_tail() is None
    _append_turn(archive, 1, "输入", "输出")
    before = tuple(path.read_bytes() for path in sorted((tmp_path / "raw").glob("*.jsonl")))
    assert archive.discard_pending_tail() is None
    assert tuple(path.read_bytes() for path in sorted((tmp_path / "raw").glob("*.jsonl"))) == before


def test_discard_pending_tail_rejects_wrong_or_completed_turn_without_writes(tmp_path: Path) -> None:
    archive = archive_at(tmp_path)
    _append_turn(archive, 1, "输入", "输出")
    pending = archive.append(
        role=Role.USER, content="pending",
        turn_id="turn-00000000-0000-4000-8000-000000000002",
        state_id="default", config_revision=REVISION,
    )
    segment = tmp_path / "raw" / "00000001.jsonl"
    before = segment.read_bytes()
    with pytest.raises(BaiError) as caught:
        archive.discard_pending_tail(
            expected_turn_id="turn-00000000-0000-4000-8000-000000000001"
        )
    assert caught.value.code == "RAW_PENDING_CONFLICT"
    assert segment.read_bytes() == before

    archive.append(
        role=Role.ASSISTANT, content="完成 pending", turn_id=pending.turn_id,
        state_id="default", config_revision=REVISION,
    )
    completed = segment.read_bytes()
    with pytest.raises(BaiError) as caught:
        archive.discard_pending_tail(expected_turn_id=pending.turn_id)
    assert caught.value.code == "RAW_PENDING_CONFLICT"
    assert segment.read_bytes() == completed


def test_discard_pending_tail_rejects_incomplete_middle_turn(tmp_path: Path) -> None:
    archive = archive_at(tmp_path)
    archive.append(
        role=Role.USER, content="非法中间 USER",
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default", config_revision=REVISION,
    )
    tail = archive.append(
        role=Role.USER, content="尾部 USER",
        turn_id="turn-00000000-0000-4000-8000-000000000002",
        state_id="default", config_revision=REVISION,
    )
    segment = tmp_path / "raw" / "00000001.jsonl"
    before = segment.read_bytes()
    with pytest.raises(BaiError) as caught:
        archive.discard_pending_tail(expected_turn_id=tail.turn_id)
    assert caught.value.code == "RAW_PENDING_INVALID"
    assert segment.read_bytes() == before


def test_discard_pending_tail_rejects_corrupt_hash_without_writes(tmp_path: Path) -> None:
    archive = archive_at(tmp_path)
    pending = archive.append(
        role=Role.USER, content="原正文",
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default", config_revision=REVISION,
    )
    segment = tmp_path / "raw" / "00000001.jsonl"
    corrupted = segment.read_bytes().replace("原正文".encode(), "伪正文".encode())
    segment.write_bytes(corrupted)
    with pytest.raises(BaiError) as caught:
        archive.discard_pending_tail(expected_turn_id=pending.turn_id)
    assert caught.value.code == "RAW_SEGMENT_INVALID"
    assert segment.read_bytes() == corrupted
