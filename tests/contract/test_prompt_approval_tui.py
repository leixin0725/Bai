"""[2026-07-20] Textual Pilot 验证两栏大纲+详情审计界面：按需加载、
折叠语义、复制完整审计文本，并支持明确拒绝。"""

import pytest

from textual.widgets import ListView

from bai_agent.debug.trace_view import TraceView
from bai_agent.debug.tui import PromptApprovalApp
from bai_agent.domain.models import (
    ContextUsageEstimate,
    Message,
    Participation,
    RequestPart,
    SourceKind,
    SourceRef,
    TrustLevel,
)
from tests.prompt_debug_fakes import FakeAdapter, make_draft


def _outline_keys(app: PromptApprovalApp) -> list[str]:
    return list(app._outline_keys)


def _detail_plain(app: PromptApprovalApp) -> str:
    return app.query_one("#detail", TraceView).plain_text


async def _select(app: PromptApprovalApp, pilot, key: str) -> None:
    app.query_one("#outline", ListView).index = app._outline_keys.index(key)
    await pilot.pause()


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
async def test_many_usage_parts_stay_inside_outline_and_detail_at_80x24() -> None:
    adapter = FakeAdapter()
    prepared = adapter.prepare(make_draft("很长的提示词正文"), 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    part_tokens = {f"message:4:record-{index:03d}:body": index + 1 for index in range(300)}
    part_total = sum(part_tokens.values())
    estimate = ContextUsageEstimate(
        status="estimated",
        estimated_input_tokens=part_total + 10,
        part_tokens=part_tokens,
        protocol_overhead_tokens=10,
        max_output_tokens=16,
        projected_peak_tokens=part_total + 26,
        context_capacity=100_000,
        projected_percent=(part_total + 26) / 100_000 * 100,
        projected_remaining_tokens=100_000 - part_total - 26,
        risk="normal",
    )
    app = PromptApprovalApp(prepared, payload, estimate, color_policy="never")

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        usage = app.query_one("#usage")
        outline = app.query_one("#outline", ListView)
        detail = app.query_one("#detail", TraceView)
        actions = app.query_one("#actions")
        assert usage.region.height <= 4
        assert outline.region.height > 0
        assert detail.region.height > 0
        assert detail.max_scroll_y > 0
        assert actions.region.bottom <= app.screen.region.bottom
        assert not app.query_one("#approve").disabled

    usage_text = app._usage_text()
    trace_text = app._trace_text()
    assert "分段=300项（完整明细见下框）" in usage_text
    assert trace_text.startswith("[最终 provider 载荷]")
    assert trace_text.index("[上下文分段估算]") > trace_text.index("很长的提示词正文")
    assert "message:4:record-000:body≈1" in trace_text
    assert "message:4:record-299:body≈300" in trace_text


@pytest.mark.asyncio
async def test_outline_navigation_updates_detail_and_tab_switches_focus_at_80x24() -> None:
    adapter = FakeAdapter()
    draft = make_draft("第一段正文")
    prepared = adapter.prepare(draft, 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    estimate = ContextUsageEstimate(status="unavailable", max_output_tokens=16, reason="不可估算")
    app = PromptApprovalApp(prepared, payload, estimate, color_policy="never")

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        outline = app.query_one("#outline", ListView)
        detail = app.query_one("#detail", TraceView)
        assert app.focused is outline
        assert app._selected_key == "usage"
        await pilot.press("down")
        await pilot.pause()
        assert app._selected_key == "part:0"
        assert "第一段正文" in _detail_plain(app)
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is detail
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is outline


@pytest.mark.asyncio
async def test_whitespace_and_sources_are_hidden_by_default_expandable_and_copy_losslessly_at_80x24() -> None:
    adapter = FakeAdapter()
    draft = make_draft("A\nB")
    source = draft.parts[0].sources
    first = "rec-00000000-0000-4000-8000-000000000001"
    second = "rec-00000000-0000-4000-8000-000000000002"
    separator_source_id = "runtime:separator-only"
    separator_source = (
        SourceRef(
            source_kind=SourceKind.RUNTIME,
            source_id=separator_source_id,
            entity_ids=(second,),
            producer="separator_test",
        ),
    )
    separator_part_id = f"message:0:{second}:entry-separator"
    parts = (
        RequestPart(
            part_id=f"message:0:{first}:body", order=0,
            participation=Participation.INCLUDED, trust=TrustLevel.UNTRUSTED_DATA,
            payload_pointer="/messages/0/content", text_span=(0, 1), content="A", sources=source,
        ),
        RequestPart(
            part_id=separator_part_id, order=1,
            participation=Participation.INCLUDED, trust=TrustLevel.UNTRUSTED_DATA,
            payload_pointer="/messages/0/content", text_span=(1, 2), content="\n",
            sources=separator_source,
        ),
        RequestPart(
            part_id=f"message:0:{second}:body", order=2,
            participation=Participation.INCLUDED, trust=TrustLevel.UNTRUSTED_DATA,
            payload_pointer="/messages/0/content", text_span=(2, 3), content="B", sources=source,
        ),
    )
    request = draft.request.model_copy(
        update={"messages": (Message(role="user", content="A\nB"),)}
    )
    prepared = adapter.prepare(draft.model_copy(update={"request": request, "parts": parts}), 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    estimate = ContextUsageEstimate(status="unavailable", max_output_tokens=16, reason="不可估算")
    app = PromptApprovalApp(prepared, payload, estimate, color_policy="never")

    compact = app._trace_renderable()
    audit = app._trace_text()
    assert separator_part_id not in compact.plain
    assert separator_source_id not in compact.plain
    assert "<换行 1>" not in compact.plain
    assert separator_part_id in audit
    assert separator_source_id in audit
    assert "\\n" in audit
    assert "来源数=1" in compact.plain
    assert "类型=runtime" not in compact.plain
    assert "路径=无" not in compact.plain
    assert "source_id=input-1" not in compact.plain
    assert "entity_ids=turn-1" not in compact.plain
    assert "类型=runtime" in audit
    assert "路径=无" in audit
    assert "source_id=input-1" in audit
    assert "entity_ids=turn-1" in audit
    assert "entity_ids=来源关联的实体 UUID/标识，不是聊天顺序编号" in compact.plain
    assert "trusted_metadata=可信元数据" in compact.plain
    assert compact.spans == []
    assert "\x1b" not in audit

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        outline = app.query_one("#outline", ListView)
        assert outline.region.height > 0
        assert not app.query_one("#approve").disabled
        # 折叠模式：大纲不含空白 part，选中普通 part 的详情不含来源明细
        assert "part:1" not in _outline_keys(app)
        await _select(app, pilot, "part:2")
        await pilot.pause()
        folded = _detail_plain(app)
        assert "B" in folded
        assert "类型=runtime" not in folded
        assert "source_id=input-1" not in folded
        # 展开：大纲出现空白 part，详情出现来源明细
        await pilot.press("w")
        await pilot.pause()
        assert "part:1" in _outline_keys(app)
        await _select(app, pilot, "part:1")
        assert "\\n" in _detail_plain(app)
        assert separator_source_id in _detail_plain(app)
        await _select(app, pilot, "part:2")
        assert "类型=runtime" in _detail_plain(app)
        assert "source_id=input-1" in _detail_plain(app)
        # 再次折叠
        await pilot.press("w")
        await pilot.pause()
        assert "part:1" not in _outline_keys(app)
        await pilot.press("c")
        assert app._clipboard == audit


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["shortcut", "button"])
async def test_tui_copies_the_entire_audit_text_without_deciding(trigger: str) -> None:
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
    assert app._outline_keys == []
    assert app._selected_key is None
    assert app._audit_text is None
