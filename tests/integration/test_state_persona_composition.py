"""[2026-07-19] 三个状态复用同一提示/记忆核心，并按配置顺序记录状态。"""

from pathlib import Path
from shutil import copytree

import pytest

from bai_agent.config.loader import load_config
from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import StateResolutionContext, StateResolutionResult
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.runtime.controller import SingleTurnController
from tests.fakes import FakeProvider


REVISION = "sha256:" + "1" * 64


def fixture_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    copytree("config", target)
    overlay = Path("tests/fixtures/config-three-states")
    (target / "states.toml").write_bytes((overlay / "states.toml").read_bytes())
    for prompt in (overlay / "personas" / "states").glob("*.md"):
        (target / "personas" / "states" / prompt.name).write_bytes(prompt.read_bytes())
    return target


class SelectedStateResolver:
    def __init__(self, state_id: str, personas: tuple[str, ...]) -> None:
        self.state_id = state_id
        self.personas = personas

    def resolve(self, context: StateResolutionContext) -> StateResolutionResult:
        return StateResolutionResult(
            state_id=self.state_id,
            ordered_persona_ids=self.personas,
            resolver_id="test-selected",
            resolver_version="1",
            reason_code="test_fixture",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_id", "persona_ids", "prompts"),
    [
        ("default", ("state_default",), ("[STATE_DEFAULT]",)),
        ("focused", ("state_focused", "state_precise"), ("[STATE_FOCUSED]", "[STATE_PRECISE]")),
        ("bare", (), ()),
    ],
)
async def test_three_states_compose_in_order_and_persist_state(
    tmp_path: Path, state_id: str, persona_ids: tuple[str, ...], prompts: tuple[str, ...]
) -> None:
    archive = RawRecordArchive(tmp_path / state_id)
    provider = FakeProvider()
    controller = SingleTurnController(
        archive,
        provider,
        SelectedStateResolver(state_id, persona_ids),
        PromptAssembler.mvp("[BASE_PERSONA]", prompts),
    )
    await controller.run_turn("输入", config_revision=REVISION)
    rendered = "\n".join(item.content for item in provider.requests[0].messages)
    positions = [rendered.index(marker) for marker in ("[BASE_PERSONA]", *prompts)]
    assert positions == sorted(positions)
    assert {item.state_id for item in archive.read_all()} == {state_id}


def test_fixture_loads_all_states_and_missing_reference_fails(tmp_path: Path) -> None:
    config = fixture_config(tmp_path)
    snapshot = load_config(config, require_credentials=False)
    assert {item.persona_id for item in snapshot.personas if item.role == "state"} == {
        "state_default", "state_focused", "state_precise"
    }
    (config / "personas" / "states" / "focused.md").unlink()
    with pytest.raises(BaiError):
        load_config(config, require_credentials=False)

