"""[2026-07-20] 上下文估算测试固定守恒、输出预留、容量和风险语义。"""

from bai_agent.model_calls.estimation import DeepSeekCharacterEstimator
from tests.prompt_debug_fakes import FakeAdapter, make_draft


def estimate(content: str, *, capacity: int | None = 1000, output: int = 100):
    adapter = FakeAdapter()
    prepared = adapter.prepare(make_draft(content), 1).model_copy(
        update={"max_output_tokens": output}
    )
    payload = adapter.materialize_sdk_kwargs(prepared)
    return DeepSeekCharacterEstimator(
        context_capacity=capacity,
        safety_margin_percent=10,
        high_percent=80,
        critical_percent=95,
    ).estimate(prepared, payload)


def test_estimate_conserves_parts_and_peak() -> None:
    result = estimate("中文 and English 🙂")
    assert result.estimated_input_tokens == sum(result.part_tokens.values()) + result.protocol_overhead_tokens
    assert result.projected_peak_tokens == result.estimated_input_tokens + 100
    assert result.projected_remaining_tokens == 1000 - result.projected_peak_tokens


def test_risk_boundaries_and_unknown_capacity() -> None:
    assert estimate("x", capacity=1000, output=100).risk == "normal"
    assert estimate("x" * 3000, capacity=1000, output=0).risk in {"high", "critical", "exceeded"}
    assert estimate("x" * 5000, capacity=1000, output=100).risk == "exceeded"
    unknown = estimate("x", capacity=None, output=100)
    assert unknown.context_capacity is None
    assert unknown.projected_percent is None and unknown.projected_remaining_tokens is None
