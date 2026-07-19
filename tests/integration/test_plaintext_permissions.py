"""[2026-07-19] 明文长期存储可人工读取，但任何持久入口都拒绝测试凭据。"""

from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import Role
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.long_term import LongTermStore


REVISION = "sha256:" + "1" * 64
CONTROLLED = "sk-" + "runtime-controlled-value-1234567890"


def test_long_term_and_last_valid_are_plaintext_with_private_permission(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    store = LongTermStore(tmp_path, archive)
    store.initialize()
    assert "coverage_overview" in store.path.read_text(encoding="utf-8")
    assert store.validate_permissions().status.value == "private"
    assert store.last_valid_path.read_text(encoding="utf-8")


def test_credentials_never_reach_raw_long_term_or_tool_result(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    with pytest.raises(BaiError):
        archive.append(
            role=Role.USER, content=CONTROLLED, turn_id="turn-00000000-0000-4000-8000-000000000001", state_id="default", config_revision=REVISION
        )
    store = LongTermStore(tmp_path, archive)
    store.initialize()
    with pytest.raises(BaiError):
        store.initialize_with_manual_memory(CONTROLLED, ())
    assert CONTROLLED not in store.path.read_text(encoding="utf-8")
