"""[2026-07-20] CLI 在事务恢复后默认丢弃 pending，只有显式 resume 重发旧内容。"""

import io
from pathlib import Path

import pytest

from bai_agent.cli import main
from bai_agent.domain.models import Role
from bai_agent.memory.archive import RawRecordArchive


REVISION = "sha256:" + "9" * 64


class ArchiveBackedApp:
    def __init__(self, archive: RawRecordArchive) -> None:
        self.archive = archive
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def discard_pending(self, expected_turn_id=None):
        return self.archive.discard_pending_tail(expected_turn_id=expected_turn_id)

    async def run_turn(self, content, **kwargs):
        self.calls.append((content, kwargs))
        return "ok"

    def close(self):
        self.closed = True


def _pending_archive(tmp_path: Path) -> tuple[RawRecordArchive, str]:
    archive = RawRecordArchive(tmp_path / "memory")
    pending = archive.append(
        role=Role.USER, content="old-sensitive-pending",
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default", config_revision=REVISION,
    )
    return archive, pending.turn_id


@pytest.mark.parametrize("debug", [False, True])
def test_default_start_discards_pending_then_accepts_new_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, debug: bool
) -> None:
    archive, _ = _pending_archive(tmp_path)
    app = ArchiveBackedApp(archive)
    monkeypatch.setattr("bai_agent.application.build_application", lambda *args, **kwargs: app)
    monkeypatch.setattr("bai_agent.debug.tui.preflight_debug_terminal", lambda *args, **kwargs: None)
    monkeypatch.setattr("sys.stdin", io.StringIO("new input\n"))
    args = ["chat", *(["--debug-prompts"] if debug else [])]
    assert main(args) == 0
    assert [item[0] for item in app.calls] == ["new input"]
    assert archive.pending_turn() is None


def test_explicit_resume_is_only_path_that_uses_old_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, turn_id = _pending_archive(tmp_path)
    app = ArchiveBackedApp(archive)
    monkeypatch.setattr("bai_agent.application.build_application", lambda *args, **kwargs: app)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["chat", "--resume-pending"]) == 0
    assert app.calls == [("old-sensitive-pending", {"resume_pending": True, "turn_id": turn_id})]
    assert len(archive.read_all()) == 1


@pytest.mark.parametrize("mode", [[], ["--discard-pending"], ["--resume-pending"]])
def test_all_modes_enter_input_when_no_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: list[str]
) -> None:
    app = ArchiveBackedApp(RawRecordArchive(tmp_path / "memory"))
    monkeypatch.setattr("bai_agent.application.build_application", lambda *args, **kwargs: app)
    monkeypatch.setattr("sys.stdin", io.StringIO("new input\n"))
    assert main(["chat", *mode]) == 0
    assert [item[0] for item in app.calls] == ["new input"]
