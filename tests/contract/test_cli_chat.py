"""[2026-07-19] CLI 不出现会话命令，并明确报告 pending turn 和恢复语义。"""

import io
from types import SimpleNamespace

import pytest

from bai_agent.cli import _parser, main


def test_cli_has_chat_without_session_or_thread_commands() -> None:
    help_text = _parser().format_help().lower()
    assert "chat" in help_text
    assert "session" not in help_text
    assert "thread" not in help_text


def test_chat_accepts_only_explicit_pending_resume() -> None:
    args = _parser().parse_args(["chat", "--resume-pending"])
    assert args.command == "chat"
    assert args.resume_pending is True


class FakeApp:
    def __init__(self, pending=None, failure=None) -> None:
        self.archive = SimpleNamespace(pending_turn=lambda: pending)
        self.failure = failure
        self.calls = []
        self.closed = False

    async def run_turn(self, content, **kwargs):
        self.calls.append((content, kwargs))
        if self.failure:
            raise self.failure
        return "ok"

    def close(self):
        self.closed = True


def test_pending_requires_opt_in_and_resume_reuses_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = SimpleNamespace(turn_id="turn-pending", content="已保存输入")
    app = FakeApp(pending)
    monkeypatch.setattr("bai_agent.application.build_application", lambda *args, **kwargs: app)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["chat"]) == 5
    assert app.calls == []

    app.closed = False
    assert main(["chat", "--resume-pending"]) == 0
    assert app.calls == [("已保存输入", {"resume_pending": True, "turn_id": "turn-pending"})]
    assert app.closed


def test_eof_and_keyboard_interrupt_have_stable_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FakeApp()
    monkeypatch.setattr("bai_agent.application.build_application", lambda *args, **kwargs: app)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["chat"]) == 0
    assert app.closed

    interrupted = FakeApp(failure=KeyboardInterrupt())
    monkeypatch.setattr("bai_agent.application.build_application", lambda *args, **kwargs: interrupted)
    monkeypatch.setattr("sys.stdin", io.StringIO("一条输入\n"))
    assert main(["chat"]) == 130
    assert interrupted.closed
