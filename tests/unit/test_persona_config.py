"""[2026-07-19] 人格入口按职责独立，缺失、空白、重复和非法编码均 fail closed。"""

from pathlib import Path
from shutil import copytree

import pytest

from bai_agent.config.loader import load_config
from bai_agent.domain.errors import BaiError


def prepared(tmp_path: Path, marker: str = "a") -> Path:
    target = tmp_path / "config"
    copytree("config", target)
    fixture = Path(f"tests/fixtures/config-persona-{marker}/personas/chat.md")
    (target / "personas" / "chat.md").write_bytes(fixture.read_bytes())
    return target


def test_two_legal_chat_personas_have_distinct_stable_markers(tmp_path: Path) -> None:
    first = load_config(prepared(tmp_path / "a", "a"), require_credentials=False)
    second = load_config(prepared(tmp_path / "b", "b"), require_credentials=False)
    assert "[PERSONA_A_STABLE_MARKER]" in next(p.prompt for p in first.personas if p.role == "chat")
    assert "[PERSONA_B_STABLE_MARKER]" in next(p.prompt for p in second.personas if p.role == "chat")
    assert first.revision != second.revision


@pytest.mark.parametrize("mode", ["missing", "blank", "duplicate", "invalid_utf8", "oversized"])
def test_invalid_persona_files_are_rejected(tmp_path: Path, mode: str) -> None:
    config = prepared(tmp_path)
    chat = config / "personas" / "chat.md"
    if mode == "missing":
        chat.unlink()
    elif mode == "blank":
        chat.write_text(" \n", encoding="utf-8")
    elif mode == "duplicate":
        text = (config / "agent.toml").read_text(encoding="utf-8")
        text = text.replace('memory_curator = "personas/memory_curator.md"', 'memory_curator = "personas/chat.md"')
        (config / "agent.toml").write_text(text, encoding="utf-8")
    elif mode == "invalid_utf8":
        chat.write_bytes(b"\xff\xfe\x00")
    else:
        chat.write_text("人格" * 140_000, encoding="utf-8")
    with pytest.raises(BaiError):
        load_config(config, require_credentials=False)


def test_chat_persona_cannot_replace_memory_curator(tmp_path: Path) -> None:
    config = prepared(tmp_path)
    (config / "personas" / "memory_curator.md").unlink()
    with pytest.raises(BaiError) as raised:
        load_config(config, require_credentials=False)
    assert raised.value.code == "CONFIG_REFERENCE_MISSING"

