"""[2026-08-08] TUI presenter 在 run_async 前后暂停/恢复 stdin 输入源，
异常路径也必须恢复，避免 TUI 结束后 shell 读取器丢失 stdin。"""

from __future__ import annotations

import pytest

from bai_agent.debug.tui import PromptApprovalApp, TextualApprovalPresenter
from bai_agent.domain.models import ApprovalDecision, ContextUsageEstimate
from tests.prompt_debug_fakes import FakeAdapter, make_draft


class _SpyInputSource:
    def __init__(self) -> None:
        self.pause_calls = 0
        self.resume_calls = 0

    async def pause(self) -> None:
        self.pause_calls += 1

    async def resume(self) -> None:
        self.resume_calls += 1


def _prepared(adapter: FakeAdapter):
    prepared = adapter.prepare(make_draft("门控正文"), 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    estimate = ContextUsageEstimate(
        status="unavailable", max_output_tokens=16, reason="不可估算"
    )
    return prepared, payload, estimate


@pytest.mark.asyncio
async def test_decide_pauses_input_source_around_tui_and_resumes_after(monkeypatch) -> None:
    adapter = FakeAdapter()
    prepared, payload, estimate = _prepared(adapter)
    source = _SpyInputSource()
    presenter = TextualApprovalPresenter(color_policy="never", input_source=source)

    async def fake_run_async(self) -> ApprovalDecision:
        assert source.pause_calls == 1
        assert source.resume_calls == 0
        return ApprovalDecision.approve(payload)

    monkeypatch.setattr(PromptApprovalApp, "run_async", fake_run_async)
    decision = await presenter.decide(prepared, payload, estimate, "警告")
    assert source.pause_calls == 1
    assert source.resume_calls == 1
    assert decision.decision.value == "approve"


@pytest.mark.asyncio
async def test_decide_resumes_input_source_when_tui_raises(monkeypatch) -> None:
    adapter = FakeAdapter()
    prepared, payload, estimate = _prepared(adapter)
    source = _SpyInputSource()
    presenter = TextualApprovalPresenter(color_policy="never", input_source=source)

    async def fake_run_async(self) -> ApprovalDecision:
        raise RuntimeError("terminal lost")

    monkeypatch.setattr(PromptApprovalApp, "run_async", fake_run_async)
    with pytest.raises(RuntimeError, match="terminal lost"):
        await presenter.decide(prepared, payload, estimate, "警告")
    assert source.pause_calls == 1
    assert source.resume_calls == 1


@pytest.mark.asyncio
async def test_decide_without_input_source_behaves_as_before(monkeypatch) -> None:
    adapter = FakeAdapter()
    prepared, payload, estimate = _prepared(adapter)
    presenter = TextualApprovalPresenter(color_policy="never")

    async def fake_run_async(self) -> ApprovalDecision:
        return ApprovalDecision.reject(payload)

    monkeypatch.setattr(PromptApprovalApp, "run_async", fake_run_async)
    decision = await presenter.decide(prepared, payload, estimate, "警告")
    assert decision.decision.value == "reject"
