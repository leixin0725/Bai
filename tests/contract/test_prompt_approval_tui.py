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


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["shortcut", "button"])
async def test_tui_copies_the_entire_trace_box_without_deciding(trigger: str) -> None:
    adapter = FakeAdapter()
    prepared = adapter.prepare(make_draft("要复制的完整正文\n第二行"), 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    estimate = ContextUsageEstimate(status="unavailable", max_output_tokens=16, reason="不可估算")
    app = PromptApprovalApp(prepared, payload, estimate, color_policy="never")
    expected = app._trace_text()

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert "剪贴板" in str(app.query_one("#warning").content)
        if trigger == "shortcut":
            await pilot.press("c")
        else:
            await pilot.click("#copy")
        await pilot.pause()

        assert app._clipboard == expected
        assert "[最终 provider 载荷]" in app._clipboard
        assert "要复制的完整正文\n第二行" in app._clipboard
        assert app.decision is None
        assert app.request is prepared and app.payload is payload and app.estimate is estimate


@pytest.mark.asyncio
@pytest.mark.parametrize("key,interrupted", [("escape", False), ("ctrl+c", True)])
async def test_tui_escape_and_ctrl_c_never_approve(key: str, interrupted: bool) -> None:
    adapter = FakeAdapter()
    prepared = adapter.prepare(make_draft("退出路径正文"), 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    estimate = ContextUsageEstimate(status="unavailable", max_output_tokens=16, reason="不可估算")
    app = PromptApprovalApp(prepared, payload, estimate, color_policy="never")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press(key)
    assert app.decision and app.decision.decision.value == "reject"
    assert app.interrupted is interrupted
    assert app.request is None and app.payload is None and app.estimate is None
