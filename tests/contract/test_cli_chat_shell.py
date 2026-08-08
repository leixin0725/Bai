"""[2026-08-08] chat 命令切换到运行时外壳后的 CLI 契约：逐行处理、pending 与关闭。"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from bai_agent.cli import main
from tests.fakes import FakeApplication


def test_cli_chat_processes_lines_through_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FakeApplication()
    monkeypatch.setattr("bai_agent.application.build_application", lambda *a, **k: app)
    monkeypatch.setattr("sys.stdin", io.StringIO("第一行\n第二行\n"))
    assert main(["chat"]) == 0
    assert app.calls == [
        ("第一行", {"reload_config": False}),
        ("第二行", {"reload_config": False}),
    ]
    assert app.closed


def test_cli_chat_empty_stdin_exits_zero_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FakeApplication()
    monkeypatch.setattr("bai_agent.application.build_application", lambda *a, **k: app)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["chat"]) == 0
    assert app.calls == []
    assert app.closed


def test_cli_chat_resume_pending_goes_through_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = SimpleNamespace(turn_id="turn-pending", content="旧正文")
    app = FakeApplication(pending=pending)
    monkeypatch.setattr("bai_agent.application.build_application", lambda *a, **k: app)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["chat", "--resume-pending"]) == 0
    assert app.calls == [
        ("旧正文", {"reload_config": False, "resume_pending": True, "turn_id": "turn-pending"})
    ]
    assert app.closed


def test_cli_chat_discard_pending_keeps_json_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pending = SimpleNamespace(turn_id="turn-pending", content="旧正文")
    app = FakeApplication(pending=pending)
    monkeypatch.setattr("bai_agent.application.build_application", lambda *a, **k: app)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["chat"]) == 0
    output = capsys.readouterr().out
    assert '"pending_discarded":true' in output
    assert '"pending_turn_id":"turn-pending"' in output
    assert app.discards == ["turn-pending"]
    assert app.calls == []
    assert app.closed
