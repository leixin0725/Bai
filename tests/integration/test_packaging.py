"""[2026-07-19] 包安装后的入口、编码、权限和原子路径必须在受支持平台保持同一语义。"""

from __future__ import annotations

from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bai_agent.config.loader import default_config_dir

from bai_agent.domain.models import Role
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.recovery import atomic_write, find_temporary_files
from bai_agent.security.permissions import PermissionStatus, ensure_private_path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "compatibility.yml"


def test_installed_package_metadata_and_module_entry(tmp_path: Path) -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["requires-python"] == ">=3.12,<3.15"
    assert version("bai-agent") == metadata["project"]["version"]
    help_result = subprocess.run(
        [sys.executable, "-m", "bai_agent", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "chat" in help_result.stdout
    assert "memory" in help_result.stdout

    validate = subprocess.run(
        [
            sys.executable,
            "-m",
            "bai_agent",
            "--config-dir",
            str(ROOT / "config"),
            "--data-dir",
            str(tmp_path / "data"),
            "memory",
            "validate",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(validate.stdout)
    assert payload["ok"] is True
    assert payload["coverage_gaps"] == 0


def test_local_atomic_utf8_and_permission_normalization(tmp_path: Path) -> None:
    target = tmp_path / "本地原子路径" / "内容.txt"
    atomic_write(target, "第一版：简体中文。\n".encode("utf-8"))
    atomic_write(target, "第二版：跨平台 UTF-8。\n".encode("utf-8"))
    assert target.read_text(encoding="utf-8") == "第二版：跨平台 UTF-8。\n"
    assert find_temporary_files(target.parent) == ()
    permission = ensure_private_path(target, is_directory=False)
    assert permission.status in {PermissionStatus.PRIVATE, PermissionStatus.UNVERIFIABLE}
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600

    archive = RawRecordArchive(tmp_path / "portable-memory")
    record = archive.append(
        role=Role.USER,
        content="跨平台原始记录：你好。",
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default",
        config_revision="sha256:" + "8" * 64,
    )
    assert archive.read_all() == (record,)
    assert "你好" in next((tmp_path / "portable-memory" / "raw").glob("*.jsonl")).read_text(
        encoding="utf-8"
    )


def test_compatibility_workflow_covers_supported_matrix() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in workflow
    assert "ubuntu-24.04" in workflow
    assert "macos-latest" not in workflow
    assert "'3.13'" in workflow
    assert "'3.14'" in workflow
    assert "'3.12'" in workflow
    assert "actions/checkout@v7" in workflow
    assert "actions/setup-python@v6" in workflow
    assert 'pytest -m "not performance"' in workflow
    assert "test_prompt_tui_latency.py" in workflow
    assert "run_prompt_tui_performance" in workflow
    assert "BAI_RUN_WINDOWS_REFERENCE" not in workflow
    assert "if: github.event_name == 'workflow_dispatch'" in workflow


def test_tzdata_fallback_and_packaged_default_timestamp_config_are_reachable() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(item.startswith("tzdata>=2026.3") for item in project["project"]["dependencies"])
    force_include = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_include["config"] == "bai_agent/default_config"
    discovered = default_config_dir()
    assert (discovered / "history_timestamps.toml").is_file()
    local = datetime(2026, 7, 20, tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    assert local.strftime("%Y-%m-%d %H:%M %z") == "2026-07-20 08:00 +0800"


def test_built_wheel_contains_complete_default_config(tmp_path: Path) -> None:
    subprocess.run(
        [
            sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps",
            "--no-build-isolation", "--wheel-dir", str(tmp_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    wheel = next(tmp_path.glob("bai_agent-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "bai_agent/default_config/history_timestamps.toml" in names
        assert "bai_agent/default_config/agent.toml" in names
        timestamp_text = archive.read(
            "bai_agent/default_config/history_timestamps.toml"
        ).decode("utf-8")
    assert 'display_timezone = "Asia/Shanghai"' in timestamp_text
