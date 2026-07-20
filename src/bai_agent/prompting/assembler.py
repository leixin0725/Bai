"""[2026-07-19] 提示组装以固定段序和显式信任级别表达完整上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    PromptContext,
    PromptSegment,
    ConfigAsset,
    Participation,
    RawRecord,
    RequestPart,
    SourceKind,
    SourceRef,
    StateResolutionResult,
    TrustLevel,
    MemoryCoverageOverview,
    content_hash,
)
from bai_agent.memory.selection import validate_complete_coverage


@dataclass(frozen=True, slots=True)
class PromptAssembler:
    base_persona: str
    state_personas: tuple[str, ...]
    base_asset: ConfigAsset | None = None
    state_assets: tuple[ConfigAsset, ...] = ()

    @classmethod
    def mvp(
        cls,
        base_persona: str,
        state_personas: tuple[str, ...],
        *,
        base_asset: ConfigAsset | None = None,
        state_assets: tuple[ConfigAsset, ...] = (),
    ) -> "PromptAssembler":
        if not base_persona.strip() or any(not item.strip() for item in state_personas):
            raise BaiError("PROMPT_SEGMENT_MISSING", "强制提示段缺失。")
        return cls(base_persona, state_personas, base_asset, state_assets)

    def assemble(
        self,
        *,
        flow_id: str,
        turn_id: str,
        config_revision: str,
        state_resolution: StateResolutionResult,
        memory_overview: str | MemoryCoverageOverview,
        long_term_memories: Iterable[str],
        long_term_source_ids: Iterable[str] = (),
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
        long_source_ids = tuple(long_term_source_ids)
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
                PromptSegment(
                    segment_id="memory_overview", trust=TrustLevel.UNTRUSTED_DATA,
                    content=overview_text or "[]",
                    source_ids=(
                        tuple(
                            source_id
                            for span in memory_overview.coverage_spans
                            for source_id in (span.batch_id, *span.record_ids)
                        )
                        if isinstance(memory_overview, MemoryCoverageOverview)
                        else ()
                    ),
                ),
                PromptSegment(
                    segment_id="long_term_memories", trust=TrustLevel.UNTRUSTED_DATA,
                    content=long_text, source_ids=long_source_ids,
                ),
                PromptSegment(
                    segment_id="recent_records",
                    trust=TrustLevel.UNTRUSTED_DATA,
                    content=recent_text,
                    source_ids=tuple(item.record_id for item in recent),
                ),
                PromptSegment(
                    segment_id="current_input", trust=TrustLevel.USER_INSTRUCTION,
                    content=current_input, source_ids=(turn_id,),
                ),
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

    def request_parts(self, context: PromptContext) -> tuple[RequestPart, ...]:
        """[2026-07-20] part 来源来自加载/选择关系，绝不根据相同正文反向搜索。"""
        parts: list[RequestPart] = []
        for index, segment in enumerate(context.segments):
            sources: tuple[SourceRef, ...]
            if segment.segment_id == "base_persona" and self.base_asset is not None:
                sources = (self._asset_source(self.base_asset),)
            elif segment.segment_id.startswith("state_persona:") and index - 1 < len(self.state_assets):
                sources = (self._asset_source(self.state_assets[index - 1]),)
            elif segment.segment_id == "current_input":
                sources = (
                    SourceRef(
                        source_kind=SourceKind.RUNTIME,
                        source_id=f"runtime:user_input:{context.turn_id}",
                        entity_ids=(context.turn_id,),
                        producer="user_input",
                    ),
                )
            elif segment.segment_id in {"memory_overview", "long_term_memories"}:
                sources = (
                    SourceRef(
                        source_kind=SourceKind.DATA_FILE,
                        source_id=f"memory:{segment.segment_id}",
                        project_relative_path="data/memory/long_term.yaml",
                        content_sha256=content_hash(segment.content),
                        revision=context.config_revision,
                        entity_ids=segment.source_ids,
                        producer="memory_selector",
                    ),
                )
            elif segment.segment_id == "recent_records":
                sources = (
                    SourceRef(
                        source_kind=SourceKind.DATA_FILE,
                        source_id="memory:recent_records",
                        project_relative_path="data/memory/raw",
                        content_sha256=content_hash(segment.content),
                        revision=context.config_revision,
                        entity_ids=segment.source_ids,
                        producer="raw_record_selector",
                    ),
                )
            else:
                sources = (
                    SourceRef(
                        source_kind=SourceKind.GENERATED,
                        source_id=f"generated:{segment.segment_id}",
                        producer="prompt_assembler",
                    ),
                )
            participation = Participation.EMPTY if segment.content == "" else Participation.INCLUDED
            parts.append(
                RequestPart(
                    part_id=f"message:{index}:{segment.segment_id}",
                    order=index,
                    participation=participation,
                    trust=segment.trust,
                    payload_pointer=f"/messages/{index}/content" if participation == Participation.INCLUDED else None,
                    text_span=(0, len(segment.content)) if participation == Participation.INCLUDED else None,
                    content=segment.content,
                    sources=sources if participation == Participation.INCLUDED else (),
                )
            )
        return tuple(parts)

    @staticmethod
    def _asset_source(asset: ConfigAsset) -> SourceRef:
        return SourceRef(
            source_kind=SourceKind.CONFIG_FILE,
            source_id=asset.asset_id,
            project_relative_path=f"config/{asset.project_relative_path}",
            content_sha256=asset.content_sha256,
            revision=asset.revision,
            producer="config_loader",
        )
