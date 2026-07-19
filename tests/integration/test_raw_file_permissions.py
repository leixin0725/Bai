"""[2026-07-19] 明文文件权限具有可移植的 private/too_broad/unverifiable 结果。"""

import os
from pathlib import Path

import pytest

from bai_agent.cli import main
from bai_agent.domain.errors import BaiError
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.security.permissions import PermissionStatus, classify_windows_acl, ensure_private_path


def test_posix_modes_or_windows_runtime_result(tmp_path: Path) -> None:
    directory = tmp_path / "memory"
    file = directory / "raw.jsonl"
    directory.mkdir()
    file.write_text("{}\n", encoding="utf-8")
    result_dir = ensure_private_path(directory, is_directory=True)
    result_file = ensure_private_path(file, is_directory=False)
    assert result_dir.status in {PermissionStatus.PRIVATE, PermissionStatus.UNVERIFIABLE}
    assert result_file.status in {PermissionStatus.PRIVATE, PermissionStatus.UNVERIFIABLE}
    if os.name != "nt":
        assert (directory.stat().st_mode & 0o777) == 0o700
        assert (file.stat().st_mode & 0o777) == 0o600


def test_windows_acl_rules_are_exact_and_fail_closed() -> None:
    allowed = [("CURRENT_USER", "allow", "read_write"), ("SYSTEM", "allow", "full")]
    assert classify_windows_acl(allowed, query_ok=True, local_path=True).status == PermissionStatus.PRIVATE
    for principal in ("Everyone", "Users", "Authenticated Users"):
        result = classify_windows_acl(allowed + [(principal, "allow", "read")], query_ok=True, local_path=True)
        assert result.status == PermissionStatus.TOO_BROAD
    assert classify_windows_acl(
        allowed + [("Guest", "allow", "read")], query_ok=True, local_path=True
    ).status == PermissionStatus.TOO_BROAD
    assert classify_windows_acl([], query_ok=False, local_path=True).status == PermissionStatus.UNVERIFIABLE
    assert classify_windows_acl([], query_ok=True, local_path=False).status == PermissionStatus.UNVERIFIABLE


@pytest.mark.parametrize("command", [["memory", "validate"], ["doctor"]])
def test_permission_anomaly_fails_validation_commands(
    monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    def fail_permissions(self):
        raise BaiError("MEMORY_PERMISSION_TOO_BROAD", "明文记忆权限过宽。")

    monkeypatch.setattr(RawRecordArchive, "validate_permissions", fail_permissions)
    assert main(["--config-dir", "config", "--data-dir", "data/permission-test", *command]) == 5
