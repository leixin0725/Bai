"""[2026-07-20] Textual Pilot 验证完整展示后才能批准，并支持明确拒绝。"""

import pytest

from bai_agent.debug.tui import PromptApprovalApp
from bai_agent.domain.models import ContextUsageEstimate
from tests.prompt_debug_fakes import FakeAdapter, make_draft


@pytest.mark.asyncio
async def test_tui_approve_and_reject_buttons_at_80x24() -> None:
    adapter = FakeAdapter()
    prepared = adapter.prepare(make_draft("正文\n🙂"), 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    estimate = ContextUsageEstimate(status="unavailable", max_output_tokens=16, reason="不可估算")
    app = PromptApprovalApp(prepared, payload, estimate, color_policy="never")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert not app.query_one("#approve").disabled
        await pilot.press("r")
    assert app.decision and app.decision.decision.value == "reject"
