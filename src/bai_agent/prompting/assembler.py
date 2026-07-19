"""[2026-07-19] 提示组装以固定段序和显式信任级别表达完整上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    PromptContext,
    PromptSegment,
    RawRecord,
    StateResolutionResult,
    TrustLevel,
    MemoryCoverageOverview,
)
from bai_agent.memory.selection import validate_complete_coverage


@dataclass(frozen=True, slots=True)
class PromptAssembler:
    base_persona: str
    state_personas: tuple[str, ...]

    @classmethod
    def mvp(cls, base_persona: str, state_personas: tuple[str, ...]) -> "PromptAssembler":
        if not base_persona.strip() or any(not item.strip() for item in state_personas):
            raise BaiError("PROMPT_SEGMENT_MISSING", "强制提示段缺失。")
        return cls(base_persona, state_personas)

    def assemble(
        self,
        *,
        flow_id: str,
        turn_id: str,
        config_revision: str,
        state_resolution: StateResolutionResult,
        memory_overview: str | MemoryCoverageOverview,
        long_term_memories: Iterable[str],
        recent_records: Iterable[RawRecord],
        current_input: str,
        all_raw_records: Iterable[RawRecord] | None = None,
        curated_through: int = 0,
        budgets: dict[str, int] | None = None,
    ) -> PromptContext:
        if len(state_resolution.ordered_persona_ids) != len(self.state_personas):
            raise BaiError("STATE_PERSONA_MISSING", "状态人格引用无法完整解析。")
        segments = [
            PromptSegment(
                segment_id="base_persona",
                trust=TrustLevel.TRUSTED_INSTRUCTION,
                content=self.base_persona,
            )
        ]
        for persona_id, content in zip(state_resolution.ordered_persona_ids, self.state_personas, strict=True):
            segments.append(
                PromptSegment(
                    segment_id=f"state_persona:{persona_id}",
                    trust=TrustLevel.TRUSTED_INSTRUCTION,
                    content=content,
                )
            )
        long_items = tuple(long_term_memories)
        recent = tuple(recent_records)
        coverage = None
        overview_text = memory_overview.text if isinstance(memory_overview, MemoryCoverageOverview) else memory_overview
        if all_raw_records is not None:
            overview = (
                memory_overview
                if isinstance(memory_overview, MemoryCoverageOverview)
                else MemoryCoverageOverview.empty()
            )
            coverage = validate_complete_coverage(
                all_raw_records,
                overview,
                curated_through=curated_through,
                recent_records=recent,
            )
        limits = budgets or {}
        overview_limit = limits.get("overview_chars", len(overview_text))
        if len(overview_text) > overview_limit:
            raise BaiError("PROMPT_BUDGET_EXCEEDED", "记忆覆盖概览超过配置预算。")
        long_limit = limits.get("long_term_chars", sum(len(item) for item in long_items) + len(long_items))
        selected_long: list[str] = []
        used_long = 0
        for item in long_items:
            if used_long + len(item) > long_limit:
                continue
            selected_long.append(item)
            used_long += len(item)
        long_text = "\n".join(selected_long) or "[]"
        recent_text = "\n".join(f"{item.role.value}: {item.content}" for item in recent)
        if len(recent_text) > limits.get("recent_chars", len(recent_text)):
            raise BaiError("PROMPT_BUDGET_EXCEEDED", "近期原文超过预算，必须先完成整理。")
        recent_text = recent_text or "[]"
        segments.extend(
            [
                PromptSegment(segment_id="memory_overview", trust=TrustLevel.UNTRUSTED_DATA, content=overview_text or "[]"),
                PromptSegment(segment_id="long_term_memories", trust=TrustLevel.UNTRUSTED_DATA, content=long_text),
                PromptSegment(
                    segment_id="recent_records",
                    trust=TrustLevel.UNTRUSTED_DATA,
                    content=recent_text,
                    source_ids=tuple(item.record_id for item in recent),
                ),
                PromptSegment(segment_id="current_input", trust=TrustLevel.USER_INSTRUCTION, content=current_input),
            ]
        )
        required = {"base_persona", "memory_overview", "long_term_memories", "recent_records", "current_input"}
        if required - {item.segment_id for item in segments}:
            raise BaiError("PROMPT_SEGMENT_MISSING", "强制提示段缺失。")
        manifest = [
            {"source_id": source_id, "segment_id": segment.segment_id}
            for segment in segments
            for source_id in segment.source_ids
        ]
        if isinstance(memory_overview, MemoryCoverageOverview):
            for span in memory_overview.coverage_spans:
                manifest.append(
                    {"source_id": span.batch_id, "segment_id": "memory_overview"}
                )
                manifest.extend(
                    {
                        "source_id": record_id,
                        "segment_id": "memory_overview",
                        "sha256": digest,
                    }
                    for record_id, digest in zip(
                        span.record_ids, span.record_hashes, strict=True
                    )
                )
        return PromptContext(
            flow_id=flow_id,
            turn_id=turn_id,
            config_revision=config_revision,
            state_resolution=state_resolution,
            segments=tuple(segments),
            source_manifest=tuple(manifest),
            coverage=(
                {
                    "covered_range": list(coverage.covered_range),
                    "direct_range": list(coverage.direct_range),
                }
                if coverage
                else {}
            ),
        )
