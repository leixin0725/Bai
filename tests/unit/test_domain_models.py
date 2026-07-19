"""[2026-07-19] 领域 DTO 的稳定性测试覆盖序列化和拒绝边界。"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from bai_agent.domain.models import RawRecord, Role, canonical_json, content_hash, new_id, utc_now


def test_raw_record_is_frozen_and_json_round_trips() -> None:
    created = datetime(2026, 7, 19, tzinfo=timezone.utc)
    record = RawRecord.create(
        record_id="rec-00000000-0000-4000-8000-000000000001",
        global_sequence=1,
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        role=Role.USER,
        content="你好\n世界",
        created_at=created,
        state_id="default",
        config_revision="sha256:" + "1" * 64,
    )

    restored = RawRecord.model_validate_json(record.model_dump_json())
    assert restored == record
    assert restored.content_sha256 == content_hash("你好\n世界")
    with pytest.raises(ValidationError):
        record.content = "不能修改"  # type: ignore[misc]


@pytest.mark.parametrize("prefix", ["rec", "turn", "flow", "mem", "batch"])
def test_generated_ids_have_stable_prefix(prefix: str) -> None:
    assert new_id(prefix).startswith(f"{prefix}-")


def test_utc_clock_and_canonical_json_are_deterministic() -> None:
    assert utc_now().utcoffset() == timezone.utc.utcoffset(None)
    assert canonical_json({"乙": 2, "甲": 1}) == '{"乙":2,"甲":1}'


def test_invalid_role_and_naive_time_are_rejected() -> None:
    payload = {
        "schema_version": 1,
        "record_id": "rec-00000000-0000-4000-8000-000000000001",
        "global_sequence": 1,
        "turn_id": "turn-00000000-0000-4000-8000-000000000001",
        "role": "tool",
        "content": "文本",
        "created_at": "2026-07-19T00:00:00",
        "state_id": "default",
        "config_revision": "sha256:" + "1" * 64,
        "content_sha256": content_hash("文本"),
    }
    with pytest.raises(ValidationError):
        RawRecord.model_validate(payload)

