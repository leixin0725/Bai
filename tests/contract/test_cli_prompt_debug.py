"""[2026-07-20] 调试 CLI 在进入应用前完成 TTY/TUI 门禁，并保持进程级开关语义。"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from bai_agent.cli import _parser, main
from bai_agent.domain.errors import DebugPresentationError, TurnInterrupted


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_debug_flag_defaults_off_after_each_parse_and_has_no_runtime_toggle() -> None:
    assert _parser().parse_args(["chat"]).debug_prompts is False
    assert _parser().parse_args(["chat", "--debug-prompts"]).debug_prompts is True
    assert _parser().parse_args(["chat"]).debug_prompts is False
    assert "toggle" not in _parser().format_help().casefold()


@pytest.mark.parametrize("stdin_tty,stdout_tty", [(False, True), (True, False), (False, False)])
def test_debug_non_tty_fails_before_application_or_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stdin_tty: bool,
    stdout_tty: bool,
) -> None:
    class Stream(TTYBuffer):
        def __init__(self, tty: bool) -> None:
            super().__init__("私人输入\n")
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    invoked = False

    def forbidden_build(*args, **kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("门禁后不得构建应用")

    output = Stream(stdout_tty)
    monkeypatch.setattr("sys.stdin", Stream(stdin_tty))
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("bai_agent.application.build_application", forbidden_build)
    assert main(["--data-dir", str(tmp_path / "data"), "chat", "--debug-prompts"]) == 2
    assert not invoked and not (tmp_path / "data").exists()
    assert "DEBUG_TTY_REQUIRED" in output.getvalue()
    assert "私人输入" not in output.getvalue()


def test_textual_preflight_failure_is_redacted_and_precedes_application(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = TTYBuffer()
    monkeypatch.setattr("sys.stdin", TTYBuffer("不应读取\n"))
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr(
        "bai_agent.debug.tui.preflight_debug_terminal",
        lambda *args, **kwargs: (_ for _ in ()).throw(DebugPresentationError()),
    )
    monkeypatch.setattr(
        "bai_agent.application.build_application",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不得构建应用")),
    )
    assert main(["--data-dir", str(tmp_path / "data"), "chat", "--debug-prompts"]) == 2
    assert "DEBUG_PRESENTATION_FAILED" in output.getvalue()
    assert "不应读取" not in output.getvalue()
    assert not (tmp_path / "data").exists()


def test_debug_interrupt_closes_application_and_returns_130(monkeypatch: pytest.MonkeyPatch) -> None:
    class InterruptedApp:
        archive = SimpleNamespace(pending_turn=lambda: None)
        closed = False

        async def run_turn(self, content, **kwargs):
            raise TurnInterrupted()

        def close(self) -> None:
            self.closed = True

    app = InterruptedApp()
    monkeypatch.setattr("sys.stdin", TTYBuffer("一条输入\n"))
    monkeypatch.setattr("sys.stdout", TTYBuffer())
    monkeypatch.setattr("bai_agent.debug.tui.preflight_debug_terminal", lambda *args, **kwargs: None)
    monkeypatch.setattr("bai_agent.application.build_application", lambda *args, **kwargs: app)
    assert main(["chat", "--debug-prompts"]) == 130
    assert app.closed
