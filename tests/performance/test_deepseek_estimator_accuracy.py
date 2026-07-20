"""[2026-07-20] 离线参考集验证来源字段与 95% 宽容误差目标，不调用实时 API。"""

import json
from pathlib import Path

from bai_agent.domain.models import canonical_json, content_hash
from bai_agent.model_calls.estimation import estimate_text_payload


def test_offline_deepseek_fixture_has_40_cases_and_meets_accuracy_target() -> None:
    fixture = json.loads(Path("tests/fixtures/prompt_trace/deepseek_usage_cases.json").read_text(encoding="utf-8"))
    assert fixture["model"] == "deepseek-v4-flash"
    assert fixture["collected_at"] == "2026-07-20"
    assert "refresh" in fixture and "canonical JSON" in fixture["payload_hash_method"]
    assert len(fixture["cases"]) >= 40
    within = 0
    for case in fixture["cases"]:
        payload = {
            "model": fixture["model"],
            "messages": [{"role": "user", "content": case["text"]}],
        }
        assert case["payload_sha256"] == content_hash(canonical_json(payload))
        estimated = estimate_text_payload(case["text"], safety_margin_percent=10)
        actual = case["official_prompt_tokens"]
        if abs(estimated - actual) <= max(int(actual * 0.15), 128):
            within += 1
    assert within / len(fixture["cases"]) >= 0.95
