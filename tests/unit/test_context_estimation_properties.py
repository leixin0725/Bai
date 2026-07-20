"""[2026-07-20] 任意 Unicode 与片段组合的估算必须确定、非负且守恒。"""

from hypothesis import given, strategies as st

from bai_agent.model_calls.estimation import DeepSeekCharacterEstimator
from tests.prompt_debug_fakes import FakeAdapter, make_draft


@given(st.text(max_size=300))
def test_unicode_estimation_is_deterministic_nonnegative_and_conservative(text: str) -> None:
    adapter = FakeAdapter()
    prepared = adapter.prepare(make_draft(text), 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    estimator = DeepSeekCharacterEstimator(context_capacity=1_000_000)
    first = estimator.estimate(prepared, payload)
    second = estimator.estimate(prepared, payload)
    assert first == second
    assert first.estimated_input_tokens is not None and first.estimated_input_tokens >= 0
    assert all(value >= 0 for value in first.part_tokens.values())
    assert first.estimated_input_tokens == sum(first.part_tokens.values()) + first.protocol_overhead_tokens
