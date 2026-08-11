"""[2026-08-08] BR-003：整份快照原子重载；失败显式警告（分组+字段+旧 revision），禁止静默回退。"""

from __future__ import annotations

import json
from pathlib import Path
from shutil import copytree

import pytest

from bai_agent.application import build_application
from bai_agent.cli import main
from bai_agent.domain.models import HealthState
from bai_agent.runtime.shell import RuntimeShell
from tests.fakes import DeterministicClock, FakeInputSource, FakeProvider


def test_config_validate_reports_all_groups_ok(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder-invalid-for-tests")
    assert main(["--config-dir", "config", "config", "validate"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["groups"]) == {
        "agent",
        "providers",
        "states",
        "tools",
        "history_timestamps",
        "personas",
        "prompts",
    }
    assert all(value == "ok" for value in payload["groups"].values())


@pytest.mark.asyncio
async def test_failed_reload_warns_explicitly_and_keeps_old_snapshot(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    copytree("config", config)
    data = tmp_path / "data"
    app = build_application(config, data, provider=FakeProvider(response="ok"))
    warnings: list[str] = []
    shell = RuntimeShell(
        app,
        on_warning=warnings.append,
        clock=DeterministicClock(),
    )
    try:
        old_revision = app.snapshot.revision
        target = config / "history_timestamps.toml"
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "long_gap_minutes = 30", "long_gap_minutes = 0"
            ),
            encoding="utf-8",
        )
        assert await shell.run(FakeInputSource(["触发重载"], is_tty=True)) == 0
        assert len(warnings) == 1
        assert "history_timestamps" in warnings[0]
        assert "long_gap_minutes" in warnings[0]
        assert old_revision in warnings[0]
        status = shell.status_snapshot()
        assert status.health is HealthState.WARNING
        assert status.last_reload.ok is False
        assert status.last_reload.revision == old_revision
        assert status.last_reload.error is not None
        assert status.last_reload.error["group"] == "history_timestamps"
        assert status.last_reload.error["field"] == "long_gap_minutes"
        assert app.snapshot.revision == old_revision

        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "long_gap_minutes = 0", "long_gap_minutes = 45"
            ),
            encoding="utf-8",
        )
        # [2026-08-08] 一个外壳对应一次会话；修复后使用新会话外壳（同一应用实例）。
        fixed_shell = RuntimeShell(app, on_warning=warnings.append, clock=DeterministicClock())
        assert await fixed_shell.run(FakeInputSource(["修复后"], is_tty=True)) == 0
        assert len(warnings) == 1
        status = fixed_shell.status_snapshot()
        assert status.health is HealthState.OK
        assert status.last_reload.ok is True
        assert status.last_reload.revision != old_revision
    finally:
        app.close()
