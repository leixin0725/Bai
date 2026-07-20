"""[2026-07-20] Ubuntu/Python 3.13 以同进程 30 次 mounted p95 执行 500ms 强制门禁。"""

import json
from pathlib import Path
import platform
from statistics import quantiles
from time import monotonic

import pytest

from bai_agent.debug.tui import PromptApprovalApp
from bai_agent.model_calls.estimation import DeepSeekCharacterEstimator
from tests.prompt_debug_fakes import FakeAdapter, make_draft


pytestmark = pytest.mark.performance


@pytest.mark.asyncio
@pytest.mark.skipif(platform.system() != "Linux" or platform.python_version_tuple()[:2] != ("3", "13"), reason="强制门禁固定在 Ubuntu 24.04/Python 3.13")
async def test_prompt_tui_mounted_p95_under_500ms() -> None:
    baseline = json.loads(Path("tests/performance/baselines/ubuntu-24.04-python-3.13.json").read_text(encoding="utf-8"))
    adapter = FakeAdapter()
    prepared = adapter.prepare(make_draft("性能样本"), 1)
    payload = adapter.materialize_sdk_kwargs(prepared)
    estimate = DeepSeekCharacterEstimator(context_capacity=1_000_000).estimate(prepared, payload)
    timings = []
    cold_started = monotonic()
    async with PromptApprovalApp(prepared, payload, estimate).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
    cold_ms = (monotonic() - cold_started) * 1000
    for _ in range(30):
        started = monotonic()
        app = PromptApprovalApp(prepared, payload, estimate)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.display_ready
        timings.append((monotonic() - started) * 1000)
    p95 = quantiles(timings, n=100, method="inclusive")[94]
    assert cold_ms >= 0
    assert p95 <= baseline["p95_milliseconds"] == 500
    assert adapter.sent == []
