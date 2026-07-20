"""[2026-07-20] 启动先持锁收敛三态事务，随后才开放 pending、新输入与 provider。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bai_agent.application import build_application
from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import RawRecord, Role, new_id, utc_now
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.transaction import PreTurnCheckpoint, TurnUnitOfWork
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter


REVISION = "sha256:" + "6" * 64


def _record(sequence: int, turn_id: str, role: Role, content: str) -> RawRecord:
    return RawRecord.create(
        record_id=new_id("rec"), global_sequence=sequence, turn_id=turn_id, role=role,
        content=content, created_at=utc_now(), state_id="default", config_revision=REVISION,
    )


def test_ready_pending_converges_before_application_exposes_archive(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    archive = RawRecordArchive(memory_root)
    user = _record(1, new_id("turn"), Role.USER, "待恢复")
    uow = TurnUnitOfWork(memory_root, archive)
    uow.begin(PreTurnCheckpoint.capture(archive, None, "default"), user)
    uow.pending("NETWORK_UNAVAILABLE")
    adapter = FakeAdapter()
    app = build_application(Path("config"), tmp_path, provider=adapter)
    try:
        assert app.archive.pending_turn() is not None
        assert [item.content for item in app.archive.read_all()] == ["待恢复"]
        assert not uow.path.exists() and adapter.sent == []
    finally:
        app.close()


def test_ready_to_commit_converges_before_new_input(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    archive = RawRecordArchive(memory_root)
    turn_id = new_id("turn")
    user, assistant = _record(1, turn_id, Role.USER, "输入"), _record(2, turn_id, Role.ASSISTANT, "输出")
    uow = TurnUnitOfWork(memory_root, archive)
    uow.begin(PreTurnCheckpoint.capture(archive, None, "default"), user)
    uow.ready(assistant)
    app = build_application(Path("config"), tmp_path, provider=FakeAdapter())
    try:
        assert [(item.role.value, item.content) for item in app.archive.read_all()] == [
            ("user", "输入"), ("assistant", "输出")
        ]
        assert not uow.path.exists()
    finally:
        app.close()


def test_recovery_conflict_blocks_application_and_provider(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    archive = RawRecordArchive(memory_root)
    user = _record(1, new_id("turn"), Role.USER, "事务输入")
    uow = TurnUnitOfWork(memory_root, archive)
    uow.begin(PreTurnCheckpoint.capture(archive, None, "default"), user)
    uow.pending("NETWORK_UNAVAILABLE")
    archive.append(
        role=Role.USER, content="人工输入", turn_id=new_id("turn"), state_id="default",
        config_revision=REVISION,
    )
    adapter = FakeAdapter()
    with pytest.raises(BaiError) as caught:
        build_application(Path("config"), tmp_path, provider=adapter)
    assert caught.value.code == "TURN_TRANSACTION_CONFLICT"
    assert adapter.sent == [] and uow.path.exists()


def test_debug_flag_is_not_persisted_across_application_restart(tmp_path: Path) -> None:
    first = build_application(
        Path("config"), tmp_path, provider=FakeAdapter(),
        debug_prompts=True, presenter=FakePresenter(),
    )
    first.close()
    second = build_application(Path("config"), tmp_path, provider=FakeAdapter())
    try:
        assert first.debug_prompts is True
        assert second.debug_prompts is False
        assert "debug_prompts" not in second.snapshot.settings["agent.toml"]
    finally:
        second.close()
