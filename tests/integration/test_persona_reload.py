"""[2026-07-19] 人格只在轮次边界切换，确定性 oracle 读取最终模型请求而非自然语言。"""

from hashlib import sha256
from pathlib import Path
from shutil import copytree

import pytest

from bai_agent.application import build_application
from bai_agent.domain.errors import BaiError
from tests.fakes import FakeProvider


def digest_files(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        item.relative_to(path).as_posix(): sha256(item.read_bytes()).hexdigest()
        for item in path.rglob("*")
        if item.is_file() and item.name != "writer.lock"
    }


@pytest.mark.asyncio
async def test_persona_marker_and_revision_switch_only_between_turns(tmp_path: Path) -> None:
    config = tmp_path / "config"
    copytree("config", config)
    chat = config / "personas" / "chat.md"
    chat.write_bytes(Path("tests/fixtures/config-persona-a/personas/chat.md").read_bytes())
    data = tmp_path / "data"
    provider = FakeProvider(response="确定性回复")
    app = build_application(config, data, provider=provider)
    try:
        await app.run_turn("第一轮")
        first_revision = app.snapshot.revision
        first_prompt = "\n".join(message.content for message in provider.requests[-1].messages)
        historical_records = app.archive.read_all()
        long_term_hash = sha256(app.long_term_store.path.read_bytes()).hexdigest()

        chat.write_bytes(Path("tests/fixtures/config-persona-b/personas/chat.md").read_bytes())
        await app.run_turn("第二轮")
        second_revision = app.snapshot.revision
        second_prompt = "\n".join(message.content for message in provider.requests[-1].messages)
        all_records = app.archive.read_all()
    finally:
        app.close()
    assert "[PERSONA_A_STABLE_MARKER]" in first_prompt
    assert "[PERSONA_B_STABLE_MARKER]" not in first_prompt
    assert "[PERSONA_B_STABLE_MARKER]" in second_prompt
    assert first_revision != second_revision
    assert all_records[: len(historical_records)] == historical_records
    assert sha256(app.long_term_store.path.read_bytes()).hexdigest() == long_term_hash


@pytest.mark.asyncio
async def test_invalid_reload_stops_before_memory_write(tmp_path: Path) -> None:
    config = tmp_path / "config"
    copytree("config", config)
    data = tmp_path / "data"
    app = build_application(config, data, provider=FakeProvider())
    try:
        before = digest_files(data / "memory")
        (config / "personas" / "chat.md").write_text("", encoding="utf-8")
        with pytest.raises(BaiError):
            await app.run_turn("不得保存")
        assert digest_files(data / "memory") == before
    finally:
        app.close()
