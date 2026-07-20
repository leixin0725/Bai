"""[2026-07-19] 结构化追踪采用字段白名单，从类型边界阻止正文和参数写日志。"""

from __future__ import annotations

import json
from typing import TextIO

from bai_agent.domain.errors import BaiError


ALLOWED_FIELDS = frozenset(
    {
        "record_id",
        "turn_id",
        "flow_id",
        "persona_id",
        "state_id",
        "config_revision",
        "provider_id",
        "model_profile_id",
        "memory_id",
        "batch_id",
        "overview_revision",
        "error_code",
        "result_code",
        "count",
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "covered_range",
        "direct_range",
        "source_manifest",
        "source_count",
        "revision",
        "trigger_record_id",
        "call_id",
        "call_sequence",
        "attempt",
        "purpose",
        "status",
        "actual_total_tokens",
        "estimated_input_tokens",
        "cursor_present",
        "tool_id",
        "iteration",
        "stop_reason",
        "token_budget",
        "cost_budget",
    }
)


class SafeTracer:
    def __init__(self, output: TextIO) -> None:
        self._output = output

    def emit(self, event: str, **fields: object) -> None:
        invalid = set(fields) - ALLOWED_FIELDS
        if invalid:
            raise BaiError("UNSAFE_LOG_FIELD", "日志字段不在安全白名单中。")
        payload = {"event": event, **fields}
        self._output.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
