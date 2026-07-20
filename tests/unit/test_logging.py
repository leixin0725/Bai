"""[2026-07-19] 安全追踪只允许元数据字段，不接收提示或工具正文。"""

import io
import json

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.runtime.tracing import SafeTracer


def test_safe_tracer_emits_whitelisted_json() -> None:
    output = io.StringIO()
    tracer = SafeTracer(output)
    tracer.emit("turn.completed", turn_id="turn-example", count=2, duration_ms=4)
    event = json.loads(output.getvalue())
    assert event["event"] == "turn.completed"
    assert event["turn_id"] == "turn-example"
    assert "content" not in event


def test_safe_tracer_accepts_only_numeric_usage_and_call_metadata() -> None:
    output = io.StringIO()
    SafeTracer(output).emit(
        "model_call.actual_usage",
        call_id="call-example",
        call_sequence=2,
        attempt=1,
        purpose="chat",
        status="actual",
        input_tokens=20,
        output_tokens=4,
        actual_total_tokens=24,
        estimated_input_tokens=19,
    )
    event = json.loads(output.getvalue())
    assert event["actual_total_tokens"] == 24
    assert not ({"prompt", "source", "credential", "payload"} & set(event))


@pytest.mark.parametrize("field", ["content", "prompt", "arguments", "result", "authorization"])
def test_safe_tracer_rejects_body_fields(field: str) -> None:
    with pytest.raises(BaiError, match="日志字段"):
        SafeTracer(io.StringIO()).emit("unsafe", **{field: "不应记录"})
