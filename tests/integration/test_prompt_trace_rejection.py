"""[2026-07-20] fresh 与 resumed 明确拒绝都不发送并丢弃整个未完成轮次。"""

from pathlib import Path

import pytest

from bai_agent.domain.errors import TurnInterrupted, TurnRejected
from bai_agent.domain.models import Role
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.model_calls.gateway import ModelCallGateway
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.runtime.controller import SingleTurnController
from bai_agent.states.resolver import StaticStateResolver
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


@pytest.mark.asyncio
async def test_reject_sends_zero_and_clears_presenter() -> None:
    adapter, presenter = FakeAdapter(), FakePresenter(approve=False)
    gateway = ModelCallGateway(adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator())
    with pytest.raises(TurnRejected):
        await gateway.complete(make_draft())
    assert adapter.sent == [] and presenter.cleared


def _controller(tmp_path: Path, adapter: FakeAdapter, presenter) -> SingleTurnController:
    gateway = ModelCallGateway(
        adapter, debug_enabled=True, presenter=presenter,
        estimator=UnavailableEstimator(),
    )
    return SingleTurnController(
        RawRecordArchive(tmp_path), gateway, StaticStateResolver.default(),
        PromptAssembler.mvp("基础", ("状态",)), transaction_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_fresh_reject_discards_prepared_without_pending(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    controller = _controller(tmp_path, adapter, FakePresenter(approve=False))
    with pytest.raises(TurnRejected):
        await controller.run_turn("fresh reject")
    assert adapter.sent == []
    assert controller.repository.read_all() == ()
    assert not (tmp_path / ".state" / "turn-transaction.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_resumed_reject_discards_existing_raw_pending(
    tmp_path: Path, interrupted: bool
) -> None:
    class RejectingPresenter(FakePresenter):
        async def decide(self, request, payload, estimate, warning):
            if interrupted:
                raise TurnInterrupted()
            return await super().decide(request, payload, estimate, warning)

    adapter = FakeAdapter()
    presenter = RejectingPresenter(approve=False)
    controller = _controller(tmp_path, adapter, presenter)
    pending = controller.repository.append(
        role=Role.USER, content="resumed pending", turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default", config_revision="sha256:" + "1" * 64,
    )
    error = TurnInterrupted if interrupted else TurnRejected
    with pytest.raises(error):
        await controller.run_turn(
            pending.content, turn_id=pending.turn_id, resume_pending=True,
        )
    assert adapter.sent == []
    assert controller.repository.read_all() == ()
