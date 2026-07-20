"""[2026-07-20] PowerShell 启动脚本在参数绑定阶段互斥 pending 策略并安全透传。"""

from pathlib import Path
import os
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_start_script_declares_and_forwards_pending_switches() -> None:
    script = (ROOT / "start.ps1").read_text(encoding="utf-8")
    for switch, cli_flag in (
        ("ResumePending", "--resume-pending"),
        ("DiscardPending", "--discard-pending"),
        ("DebugPrompts", "--debug-prompts"),
    ):
        assert f"${switch}" in script and f'"{cli_flag}"' in script
    assert "ParameterSetName" in script


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 参数绑定是 Windows 次要兼容验收")
def test_start_script_rejects_resume_and_discard_before_body() -> None:
    command = (
        f"& '{ROOT / 'start.ps1'}' -ResumePending -DiscardPending"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT, text=True, capture_output=True, timeout=10,
    )
    combined = (result.stdout + result.stderr).casefold()
    assert result.returncode != 0
    assert "parameter set" in combined or "参数集" in combined
    assert "deepseek api key" not in combined
