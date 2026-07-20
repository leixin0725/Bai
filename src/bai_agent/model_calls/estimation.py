"""[2026-07-20] Provider-aware 离线估算明确标为近似，并以分段加协议开销保持守恒。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from math import ceil
from typing import Any

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    ContextUsageEstimate,
    Participation,
    canonical_json,
    thaw_json,
)


def _character_units(text: str) -> float:
    units = 0.0
    for character in text:
        code = ord(character)
        if code < 128:
            units += 0.25
        elif 0x3400 <= code <= 0x9FFF:
            units += 1.0
        else:
            units += 0.75
    return units


def estimate_text_payload(text: str, *, safety_margin_percent: int = 10) -> int:
    if not text:
        return 0
    base = ceil(_character_units(text))
    return ceil(base * (1 + safety_margin_percent / 100))


class DeepSeekCharacterEstimator:
    method = "deepseek_character_v1"

    def __init__(
        self,
        *,
        context_capacity: int | None,
        safety_margin_percent: int = 10,
        high_percent: int = 80,
        critical_percent: int = 95,
    ) -> None:
        self.context_capacity = context_capacity
        self.safety_margin_percent = safety_margin_percent
        self.high_percent = high_percent
        self.critical_percent = critical_percent

    def estimate(self, request, payload) -> ContextUsageEstimate:
        kwargs = thaw_json(payload.sdk_kwargs)
        messages = kwargs.get("messages") if isinstance(kwargs, dict) else None
        if not isinstance(messages, list) or any(not isinstance(item, dict) for item in messages):
            return ContextUsageEstimate(
                status="unavailable",
                max_output_tokens=request.max_output_tokens,
                context_capacity=self.context_capacity,
                reason="materialized messages 结构不受 estimator 支持。",
            )
        try:
            rendered = canonical_json(kwargs)
        except (TypeError, ValueError):
            return ContextUsageEstimate(
                status="unavailable",
                max_output_tokens=request.max_output_tokens,
                context_capacity=self.context_capacity,
                reason="materialized payload 不是可估算 JSON。",
            )
        part_tokens: dict[str, int] = {}
        for part in request.parts:
            if part.participation == Participation.INCLUDED:
                part_tokens[part.part_id] = estimate_text_payload(
                    part.content,
                    safety_margin_percent=self.safety_margin_percent,
                )
        part_total = sum(part_tokens.values())
        whole = estimate_text_payload(
            rendered,
            safety_margin_percent=self.safety_margin_percent,
        )
        # [2026-07-20] provenance 已保证正文片段不重叠；每个 marker 只在 part_total 出现一次。
        # 协议开销只补足最终物化 JSON 与已归因片段之差，保持 input = parts + overhead。
        overhead = max(0, whole - part_total)
        total = part_total + overhead
        peak = total + request.max_output_tokens
        percent = None
        remaining = None
        risk = "normal"
        if self.context_capacity is not None:
            percent = peak / self.context_capacity * 100
            remaining = self.context_capacity - peak
            if peak > self.context_capacity:
                risk = "exceeded"
            elif percent >= self.critical_percent:
                risk = "critical"
            elif percent >= self.high_percent:
                risk = "high"
        return ContextUsageEstimate(
            status="estimated",
            estimated_input_tokens=total,
            part_tokens=part_tokens,
            protocol_overhead_tokens=overhead,
            max_output_tokens=request.max_output_tokens,
            projected_peak_tokens=peak,
            context_capacity=self.context_capacity,
            projected_percent=percent,
            projected_remaining_tokens=remaining,
            risk=risk,
            method=self.method,
            confidence="conservative",
        )


class EstimatorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., Any]] = {
            "deepseek_character_v1": DeepSeekCharacterEstimator,
        }

    def create(self, estimator_id: str, **settings: Any):
        factory = self._factories.get(estimator_id)
        if factory is None:
            raise BaiError("PROVIDER_CAPABILITY_INVALID", "Estimator 未注册。")
        return factory(**settings)


def create_estimator(provider: Mapping[str, Any], profile: Mapping[str, Any], policy: Mapping[str, Any]):
    return EstimatorRegistry().create(
        str(provider["token_estimator"]),
        context_capacity=(int(profile["context_window_tokens"]) if profile.get("context_window_tokens") is not None else None),
        safety_margin_percent=int(policy["estimate_safety_margin_percent"]),
        high_percent=int(policy["high_context_percent"]),
        critical_percent=int(policy["critical_context_percent"]),
    )
