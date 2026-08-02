"""[2026-07-19] 配置与 doctor CLI 只输出引用摘要，不显示提示正文或凭据值。"""

import json
from pathlib import Path
from shutil import copytree

import pytest

from bai_agent.cli import main


def test_config_validate_reports_complete_reference_graph(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "placeholder-invalid-for-tests"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    assert main(["--config-dir", "config", "config", "validate"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["ok"]
    assert set(payload["roles"]) == {"chat", "memory_curator", "state"}
    assert payload["provider_profiles"] == ["chat", "memory_curator"]
    assert payload["tools"] == ["memory_source_query"]
    assert secret not in output
    assert "你是 Bai" not in output


def test_doctor_is_offline_and_actionable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(
        ["--config-dir", "config", "--data-dir", str(tmp_path / "doctor-data"), "doctor"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["network_probe"] is False
    assert payload["state"] == "default"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "history_timestamps.toml"),
        ("field", "long_gap_minutes"),
    ],
)
def test_timestamp_manifest_cli_error_names_actionable_path_and_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    expected: str,
) -> None:
    config = tmp_path / "config"
    copytree("config", config)
    target = config / "history_timestamps.toml"
    if mutation == "missing":
        target.unlink()
    else:
        target.write_text(
            target.read_text(encoding="utf-8").replace("long_gap_minutes = 30", "long_gap_minutes = true"),
            encoding="utf-8",
        )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder-invalid-for-tests")
    assert main(["config", "validate", "--config-dir", str(config)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert not payload["ok"]
    assert "history_timestamps.toml" in payload["error"]["message"]
    assert expected in payload["error"]["message"]
