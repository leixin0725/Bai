"""[2026-07-19] 明文文件权限在 POSIX（Ubuntu/WSL）下归一化为 private/too_broad/unverifiable。"""

from pathlib import Path

import pytest

from bai_agent.cli import main
from bai_agent.domain.errors import BaiError
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.security.permissions import PermissionStatus, ensure_private_path


def test_posix_modes_are_tightened(tmp_path: Path) -> None:
    directory = tmp_path / "memory"
    file = directory / "raw.jsonl"
    directory.mkdir()
    file.write_text("{}\n", encoding="utf-8")
    result_dir = ensure_private_path(directory, is_directory=True)
    result_file = ensure_private_path(file, is_directory=False)
    assert result_dir.status is PermissionStatus.PRIVATE
    assert result_file.status is PermissionStatus.PRIVATE
    assert (directory.stat().st_mode & 0o777) == 0o700
    assert (file.stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("command", [["memory", "validate"], ["doctor"]])
def test_permission_anomaly_fails_validation_commands(
    monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    def fail_permissions(self):
        raise BaiError("MEMORY_PERMISSION_TOO_BROAD", "明文记忆权限过宽。")

    monkeypatch.setattr(RawRecordArchive, "validate_permissions", fail_permissions)
    assert main(["--config-dir", "config", "--data-dir", "data/permission-test", *command]) == 5
