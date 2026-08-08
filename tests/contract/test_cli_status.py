"""[2026-08-08] 会话内 :status：稳定 JSON、不写 raw，并与重载失败状态一致。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from shutil import copytree

import pytest

from bai_agent.application import build_application
from bai_agent.cli import main
from bai_agent.runtime.shell import RuntimeShell
from tests.fakes import FakeApplication, FakeInputSource, FakeProvider


def test_cli_status_prints_stable_json_without_calling_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = FakeApplication()
    monkeypatch.setattr("bai_agent.application.build_application", lambda *a, **k: app)
    monkeypatch.setattr("sys.stdin", io.StringIO(":status\n"))
    assert main(["chat"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["session_state"] in {"idle", "processing", "stopping"}
    assert "last_reload" in payload
    assert "counters" in payload
    assert app.calls == []


@pytest.mark.asyncio
async def test_status_matches_failed_reload_warning(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    copytree("config", config)
    data = tmp_path / "data"
    app = build_application(config, data, provider=FakeProvider(response="ok"))
    outputs: list[str] = []
    warnings: list[str] = []
    shell = RuntimeShell(app, on_output=outputs.append, on_warning=warnings.append)
    try:
        old_revision = app.snapshot.revision
        target = config / "history_timestamps.toml"
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "long_gap_minutes = 30", "long_gap_minutes = 0"
            ),
            encoding="utf-8",
        )
        assert (
            await shell.run(
                FakeInputSource(["触发", ":status"], is_tty=True, buffered_indexes=set())
            )
            == 0
        )
        assert len(warnings) == 1
        payload = json.loads(outputs[-1])
        assert payload["health"] == "warning"
        assert payload["last_reload"]["ok"] is False
        assert payload["last_reload"]["error"]["group"] == "history_timestamps"
        assert payload["last_reload"]["error"]["field"] == "long_gap_minutes"
        assert payload["last_reload"]["revision"] == old_revision
    finally:
        app.close()
