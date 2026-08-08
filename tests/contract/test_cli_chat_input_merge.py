"""[2026-08-08] BR-006 契约：一次管道输入只产生一次处理与一条 USER 记录。"""

from __future__ import annotations

import io
from pathlib import Path
from shutil import copytree

import pytest

from bai_agent.application import build_application
from bai_agent.cli import main
from bai_agent.runtime.shell import RuntimeShell
from tests.fakes import FakeApplication, FakeInputSource, FakeProvider


def test_cli_pipe_input_merges_into_one_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FakeApplication()
    monkeypatch.setattr("bai_agent.application.build_application", lambda *a, **k: app)
    monkeypatch.setattr("sys.stdin", io.StringIO("第一行\n第二行\n第三行\n"))
    assert main(["chat"]) == 0
    assert app.calls == [("第一行\n第二行\n第三行", {"reload_config": False})]


@pytest.mark.asyncio
async def test_pipe_input_writes_one_user_and_one_assistant_record(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    copytree("config", config)
    data = tmp_path / "data"
    app = build_application(config, data, provider=FakeProvider(response="合并回复"))
    shell = RuntimeShell(app)
    try:
        assert (
            await shell.run(
                FakeInputSource(["第一行", "第二行"], is_tty=False)
            )
            == 0
        )
        records = app.archive.read_all()
        assert len(records) == 2
        assert records[0].content == "第一行\n第二行"
        assert records[1].content == "合并回复"
    finally:
        app.close()
