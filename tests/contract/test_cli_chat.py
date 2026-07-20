"""[2026-07-19] CLI 不出现会话命令，并明确报告 pending turn 和恢复语义。"""

import io
from types import SimpleNamespace

import pytest

from bai_agent.cli import _parser, main
from bai_agent.domain.errors import TurnInterrupted, TurnRejected


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
        self.discards = []
        self.closed = False

    async def run_turn(self, content, **kwargs):
        self.calls.append((content, kwargs))
        if self.failure:
            raise self.failure
        return "ok"

    def discard_pending(self, expected_turn_id=None):
        self.discards.append(expected_turn_id)
        pending = self.archive.pending_turn()
        return pending.turn_id if pending else None

    def close(self):
        self.closed = True


def test_pending_defaults_to_discard_and_resume_reuses_turn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pending = SimpleNamespace(turn_id="turn-pending", content="sensitive-pending-body")
    app = FakeApp(pending)
    monkeypatch.setattr("bai_agent.application.build_application", lambda *args, **kwargs: app)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["chat"]) == 0
    assert app.calls == []
    assert app.discards == ["turn-pending"]
    output = capsys.readouterr().out
    assert '"pending_discarded":true' in output
    assert '"pending_turn_id":"turn-pending"' in output
    assert "sensitive-pending-body" not in output and "resume_required" not in output

    app = FakeApp(pending)
    monkeypatch.setattr("bai_agent.application.build_application", lambda *args, **kwargs: app)
    assert main(["chat", "--resume-pending"]) == 0
    assert app.calls == [("sensitive-pending-body", {"resume_pending": True, "turn_id": "turn-pending"})]
    assert app.discards == []
    assert app.closed


def test_discard_and_resume_modes_are_mutually_exclusive_and_absent_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SystemExit) as caught:
        _parser().parse_args(["chat", "--resume-pending", "--discard-pending"])
    assert caught.value.code == 2

    for extra in ([], ["--discard-pending"], ["--resume-pending"]):
        app = FakeApp()
        monkeypatch.setattr("bai_agent.application.build_application", lambda *args, _app=app, **kwargs: _app)
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        assert main(["chat", *extra]) == 0
        assert app.calls == [] and app.discards == [] and app.closed


@pytest.mark.parametrize("interrupted,expected_code", [(False, 0), (True, 130)])
def test_resumed_reject_returns_to_input_but_interrupt_exits_130(
    monkeypatch: pytest.MonkeyPatch, interrupted: bool, expected_code: int
) -> None:
    pending = SimpleNamespace(turn_id="turn-pending", content="old body")

    class RejectOnceApp(FakeApp):
        async def run_turn(self, content, **kwargs):
            self.calls.append((content, kwargs))
            if len(self.calls) == 1:
                raise TurnInterrupted() if interrupted else TurnRejected()
            return "ok"

    app = RejectOnceApp(pending)
    monkeypatch.setattr("bai_agent.application.build_application", lambda *args, **kwargs: app)
    monkeypatch.setattr("sys.stdin", io.StringIO("new body\n"))
    assert main(["chat", "--resume-pending"]) == expected_code
    expected_calls = 1 if interrupted else 2
    assert len(app.calls) == expected_calls and app.closed


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
