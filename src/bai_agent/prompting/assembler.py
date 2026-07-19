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
)


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
        memory_overview: str,
        long_term_memories: Iterable[str],
        recent_records: Iterable[RawRecord],
        current_input: str,
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
        segments.extend(
            [
                PromptSegment(segment_id="memory_overview", trust=TrustLevel.UNTRUSTED_DATA, content=memory_overview or "尚无长期记忆"),
                PromptSegment(segment_id="long_term_memories", trust=TrustLevel.UNTRUSTED_DATA, content="\n".join(long_items) or "[]"),
                PromptSegment(
                    segment_id="recent_records",
                    trust=TrustLevel.UNTRUSTED_DATA,
                    content="\n".join(f"{item.role.value}: {item.content}" for item in recent) or "[]",
                    source_ids=tuple(item.record_id for item in recent),
                ),
                PromptSegment(segment_id="current_input", trust=TrustLevel.USER_INSTRUCTION, content=current_input),
            ]
        )
        required = {"base_persona", "memory_overview", "long_term_memories", "recent_records", "current_input"}
        if required - {item.segment_id for item in segments}:
            raise BaiError("PROMPT_SEGMENT_MISSING", "强制提示段缺失。")
        return PromptContext(
            flow_id=flow_id,
            turn_id=turn_id,
            config_revision=config_revision,
            state_resolution=state_resolution,
            segments=tuple(segments),
            source_manifest=tuple(
                {"source_id": source_id, "segment_id": segment.segment_id}
                for segment in segments
                for source_id in segment.source_ids
            ),
        )
