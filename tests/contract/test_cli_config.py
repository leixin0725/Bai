"""[2026-07-19] 配置与 doctor CLI 只输出引用摘要，不显示提示正文或凭据值。"""

import json

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


def test_doctor_is_offline_and_actionable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config-dir", "config", "--data-dir", "data/doctor-test", "doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["network_probe"] is False
    assert payload["state"] == "default"
