"""[2026-07-20] 批准型调试只增加门禁，不改变物化请求与确认后的领域结果。"""

from pathlib import Path

import pytest

from bai_agent.application import build_application
from bai_agent.domain.errors import DebugPresentationError
from bai_agent.domain.models import thaw_json
from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


def _record_projection(app) -> list[tuple[str, str, str, str]]:
    return [
        (item.role.value, item.content, item.state_id, item.config_revision)
        for item in app.archive.read_all()
    ]


@pytest.mark.asyncio
async def test_debug_on_off_are_deeply_equivalent_after_approval(tmp_path: Path) -> None:
    plain_adapter, debug_adapter = FakeAdapter(), FakeAdapter()
    plain = build_application(Path("config"), tmp_path / "plain", provider=plain_adapter)
    debug = build_application(
        Path("config"), tmp_path / "debug", provider=debug_adapter,
        debug_prompts=True, presenter=FakePresenter(),
    )
    try:
        assert await plain.run_turn("等价性输入") == await debug.run_turn("等价性输入")
        assert [thaw_json(item.sdk_kwargs) for item in plain_adapter.sent] == [
            thaw_json(item.sdk_kwargs) for item in debug_adapter.sent
        ]
        assert _record_projection(plain) == _record_projection(debug)
        business_states = {"prepared", "materialized", "display_ready", "sender_owned", "completed"}
        assert [
            (item.purpose, item.attempt, item.status)
            for item in plain.controller.provider.call_states if item.status in business_states
        ] == [
            (item.purpose, item.attempt, item.status)
            for item in debug.controller.provider.call_states if item.status in business_states
        ]
        assert plain.controller.tool_definitions == debug.controller.tool_definitions
    finally:
        plain.close()
        debug.close()


@pytest.mark.asyncio
async def test_presentation_failure_neither_sends_nor_rewrites_request() -> None:
    class BrokenPresenter(FakePresenter):
        async def decide(self, request, payload, estimate, warning):
            raise RuntimeError("正文不得进入外层错误")

    adapter = FakeAdapter()
    draft = make_draft("不可改写正文")
    before = draft.model_dump(mode="json")
    gateway = ModelCallGateway(
        adapter, debug_enabled=True, presenter=BrokenPresenter(),
        estimator=UnavailableEstimator(),
    )
    with pytest.raises(DebugPresentationError):
        await gateway.complete(draft)
    assert adapter.sent == []
    assert draft.model_dump(mode="json") == before
