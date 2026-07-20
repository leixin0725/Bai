"""[2026-07-20] 实际用量只形成数值摘要，不恢复已清除的提示或 TUI。"""

import pytest
from pathlib import Path

from bai_agent.application import build_application
from bai_agent.model_calls.estimation import DeepSeekCharacterEstimator
from bai_agent.model_calls.gateway import ModelCallGateway
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, make_draft


@pytest.mark.asyncio
async def test_valid_actual_usage_has_error_without_prompt_references() -> None:
    adapter, presenter = FakeAdapter(), FakePresenter()
    gateway = ModelCallGateway(
        adapter, debug_enabled=True, presenter=presenter,
        estimator=DeepSeekCharacterEstimator(context_capacity=1000),
    )
    await gateway.complete(make_draft("不得恢复的原文"))
    usage = gateway.last_actual_usage
    assert usage and usage.status == "actual" and usage.actual_total_tokens == 3
    assert not ({"prompt", "parts", "sources", "payload"} & set(type(usage).model_fields))
    assert presenter.request is None and presenter.payload is None


@pytest.mark.asyncio
async def test_missing_or_invalid_usage_is_unavailable() -> None:
    class MissingUsageAdapter(FakeAdapter):
        async def send_once(self, payload):
            result = await super().send_once(payload)
            return result.model_copy(update={"usage": {}})

    gateway = ModelCallGateway(MissingUsageAdapter(), estimator=DeepSeekCharacterEstimator(context_capacity=1000))
    await gateway.complete(make_draft())
    assert gateway.last_actual_usage and gateway.last_actual_usage.status == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1, "output_tokens": 1, "total_tokens": 0},
        {"input_tokens": 2, "output_tokens": 1, "total_tokens": 9},
    ],
)
async def test_negative_or_nonconserving_usage_is_unavailable(usage: dict[str, int]) -> None:
    class InvalidUsageAdapter(FakeAdapter):
        async def send_once(self, payload):
            result = await super().send_once(payload)
            return result.model_copy(update={"usage": usage})

    gateway = ModelCallGateway(InvalidUsageAdapter(), estimator=DeepSeekCharacterEstimator(context_capacity=1000))
    await gateway.complete(make_draft())
    assert gateway.last_actual_usage and gateway.last_actual_usage.status == "unavailable"


@pytest.mark.asyncio
async def test_controller_outputs_numeric_summary_after_prompt_presenter_is_cleared(tmp_path: Path) -> None:
    outputs: list[str] = []
    presenter = FakePresenter()
    app = build_application(
        Path("config"), tmp_path, provider=FakeAdapter(), debug_prompts=True,
        presenter=presenter, on_output=outputs.append,
    )
    try:
        await app.run_turn("用量输出测试")
    finally:
        app.close()
    assert outputs[0] == "完成"
    assert outputs[1].startswith("实际用量：输入=2，输出=1，总量=3")
    assert "用量输出测试" not in outputs[1]
    assert presenter.request is None and presenter.payload is None
