"""[2026-07-20] PowerShell 启动脚本在参数绑定阶段互斥 pending 策略并安全透传。"""

from pathlib import Path
import os
import shutil
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


def test_linux_start_script_declares_passthrough_and_key_handling() -> None:
    script = (ROOT / "start.sh").read_text(encoding="utf-8")
    for option in ("--resume-pending", "--discard-pending"):
        assert option in script
    assert "DEEPSEEK_API_KEY" in script
    assert ".venv/bin/python" in script
    assert "-m bai_agent" in script


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="需要原生 Linux Bash 验证启动入口",
)
def test_linux_start_script_rejects_resume_and_discard_before_prompt() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "start.sh"), "--resume-pending", "--discard-pending"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    combined = (result.stdout + result.stderr).casefold()
    assert result.returncode == 2
    assert "mutually exclusive" in combined
    assert "deepseek api key" not in combined


@pytest.mark.skipif(
    os.name == "nt"
    or shutil.which("bash") is None
    or not (ROOT / ".venv" / "bin" / "python").exists(),
    reason="需要原生 Linux Bash 与项目虚拟环境验证启动入口",
)
def test_linux_start_script_forwards_help_to_cli_without_key_prompt() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "start.sh"), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    combined = (result.stdout + result.stderr).casefold()
    assert result.returncode == 0
    assert "usage: bai-agent [-h]" in combined
    assert "deepseek api key" not in combined


@pytest.mark.skipif(
    os.name == "nt"
    or shutil.which("bash") is None
    or not (ROOT / ".venv" / "bin" / "python").exists(),
    reason="需要原生 Linux Bash 与项目虚拟环境验证启动入口",
)
def test_linux_start_script_injects_chat_for_chat_only_options_without_command() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "start.sh"), "--debug-prompts"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    combined = (result.stdout + result.stderr).casefold()
    assert result.returncode == 2
    assert "not an interactive terminal" in combined
    assert "required: command" not in combined


@pytest.mark.skipif(
    os.name == "nt"
    or shutil.which("bash") is None
    or not (ROOT / ".venv" / "bin" / "python").exists(),
    reason="需要原生 Linux Bash 与项目虚拟环境验证启动入口",
)
def test_linux_start_script_forwards_explicit_chat_help_to_cli() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "start.sh"), "chat", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    combined = (result.stdout + result.stderr).casefold()
    assert result.returncode == 0
    assert "usage: bai-agent chat" in combined
    assert "deepseek api key" not in combined


@pytest.mark.skipif(
    os.name == "nt"
    or shutil.which("bash") is None
    or not (ROOT / ".venv" / "bin" / "python").exists(),
    reason="需要原生 Linux Bash 与项目虚拟环境验证启动入口",
)
def test_linux_start_script_forwards_unknown_command_to_cli() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "start.sh"), "bogus"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    combined = (result.stdout + result.stderr).casefold()
    assert result.returncode == 2
    assert "invalid choice" in combined
    assert "deepseek api key" not in combined


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
