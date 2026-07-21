"""[2026-07-19] 提示组装以固定段序和显式信任级别表达完整上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    AnnotatedFragmentKind,
    PromptContext,
    PromptSegment,
    ConfigAsset,
    LongTermMemoryItem,
    Participation,
    RawRecord,
    Role,
    RequestPart,
    SourceKind,
    SourceRef,
    StateResolutionResult,
    TemporalLogEntry,
    TemporalSegmentationPolicy,
    TemporalSpan,
    TemporalTimeKind,
    TrustLevel,
    MemoryCoverageOverview,
    content_hash,
)
from bai_agent.memory.selection import validate_complete_coverage
from bai_agent.memory.temporal import MemoryTemporalProjector
from bai_agent.prompting.boundaries import (
    PromptTextPiece,
    UntrustedBoundaryRenderer,
    pieces_from_fragments,
    position_pieces,
    source_from_asset,
)
from bai_agent.prompting.temporal import annotate_history


@dataclass(frozen=True, slots=True)
class PromptAssembler:
    base_persona: str
    state_personas: tuple[str, ...]
    base_asset: ConfigAsset | None = None
    state_assets: tuple[ConfigAsset, ...] = ()
    temporal_policy: TemporalSegmentationPolicy | None = None
    boundary_renderer: UntrustedBoundaryRenderer | None = None

    @classmethod
    def mvp(
        cls,
        base_persona: str,
        state_personas: tuple[str, ...],
        *,
        base_asset: ConfigAsset | None = None,
        state_assets: tuple[ConfigAsset, ...] = (),
        temporal_policy: TemporalSegmentationPolicy | None = None,
        boundary_renderer: UntrustedBoundaryRenderer | None = None,
    ) -> "PromptAssembler":
        if not base_persona.strip() or any(not item.strip() for item in state_personas):
            raise BaiError("PROMPT_SEGMENT_MISSING", "强制提示段缺失。")
        return cls(
            base_persona,
            state_personas,
            base_asset,
            state_assets,
            temporal_policy,
            boundary_renderer,
        )

    def assemble(
        self,
        *,
        flow_id: str,
        turn_id: str,
        config_revision: str,
        state_resolution: StateResolutionResult,
        memory_overview: str | MemoryCoverageOverview,
        long_term_memories: Iterable[str | LongTermMemoryItem],
        long_term_source_ids: Iterable[str] = (),
        recent_records: Iterable[RawRecord],
        current_input_record: RawRecord | None = None,
        current_input: str | None = None,
        all_raw_records: Iterable[RawRecord] | None = None,
        curated_through: int = 0,
        budgets: dict[str, int] | None = None,
        memory_projector: MemoryTemporalProjector | None = None,
    ) -> PromptContext:
        if len(state_resolution.ordered_persona_ids) != len(self.state_personas):
            raise BaiError("STATE_PERSONA_MISSING", "状态人格引用无法完整解析。")
        if current_input_record is None:
            if current_input is None:
                raise BaiError("PROMPT_SEGMENT_MISSING", "当前输入缺失。")
            if self.temporal_policy is not None:
                raise BaiError("TEMPORAL_ENTRY_INVALID", "启用时间策略时必须提供本轮 provisional USER 记录。")
        elif current_input is not None and current_input != current_input_record.content:
            raise BaiError("TEMPORAL_ENTRY_INVALID", "当前输入字符串与 provisional 记录不一致。")
        if current_input_record is not None and (
            current_input_record.role is not Role.USER or current_input_record.turn_id != turn_id
        ):
            raise BaiError("TEMPORAL_ENTRY_INVALID", "当前输入必须来自本轮 provisional USER 记录。")
        if self.boundary_renderer is not None:
            base_source = (
                source_from_asset(self.base_asset)
                if self.base_asset is not None
                else SourceRef(
                    source_kind=SourceKind.GENERATED,
                    source_id="generated:base_persona",
                    content_sha256=content_hash(self.base_persona),
                    revision=config_revision,
                    producer="prompt_assembler",
                )
            )
            base_rendered = self.boundary_renderer.compose_system_instruction(
                self.base_persona,
                base_source,
                composition_id="chat-system",
            )
            segments = [
                PromptSegment(
                    segment_id="base_persona",
                    trust=TrustLevel.TRUSTED_INSTRUCTION,
                    content=base_rendered.text,
                    fragments=base_rendered.fragments,
                )
            ]
        else:
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
        raw_snapshot = tuple(all_raw_records) if all_raw_records is not None else None
        coverage = None
        overview_text = memory_overview.text if isinstance(memory_overview, MemoryCoverageOverview) else memory_overview
        if raw_snapshot is not None:
            overview = (
                memory_overview
                if isinstance(memory_overview, MemoryCoverageOverview)
                else MemoryCoverageOverview.empty()
            )
            coverage = validate_complete_coverage(
                raw_snapshot,
                overview,
                curated_through=curated_through,
                recent_records=recent,
                current_input_record=current_input_record,
            )
        projector = memory_projector
        if projector is None and raw_snapshot is not None:
            projector = MemoryTemporalProjector.from_records(raw_snapshot)
        overview_history = None
        if (
            self.temporal_policy is not None
            and projector is not None
            and isinstance(memory_overview, MemoryCoverageOverview)
        ):
            overview_entry = projector.project_overview(memory_overview)
            if overview_entry is not None:
                overview_history = annotate_history((overview_entry,), self.temporal_policy)
                overview_text = overview_history.text

        limits = budgets or {}
        long_history = None
        if (
            self.temporal_policy is not None
            and projector is not None
            and all(isinstance(item, LongTermMemoryItem) for item in long_items)
            and long_items
        ):
            long_entries = tuple(projector.project_memory(item) for item in long_items)
            long_history = annotate_history(long_entries, self.temporal_policy)
            long_text = long_history.text
            long_source_ids = tuple(item.memory_id for item in long_items)
        else:
            string_items = tuple(str(item) for item in long_items)
            long_limit = limits.get("long_term_chars", sum(len(item) for item in string_items) + len(string_items))
            selected_long: list[str] = []
            used_long = 0
            for item in string_items:
                if used_long + len(item) > long_limit:
                    continue
                selected_long.append(item)
                used_long += len(item)
            long_text = "\n".join(selected_long) or "[]"
        recent_history = None
        if self.temporal_policy is not None and recent:
            recent_entries = tuple(
                TemporalLogEntry(
                    entry_id=item.record_id,
                    body=f"{item.role.value}: {item.content}",
                    span=TemporalSpan(
                        start=item.created_at,
                        end=item.created_at,
                        kind=TemporalTimeKind.EVENT,
                    ),
                    sources=(
                        SourceRef(
                            source_kind=SourceKind.DATA_FILE,
                            source_id=f"raw:{item.record_id}",
                            project_relative_path="data/memory/raw",
                            content_sha256=item.content_sha256,
                            revision=config_revision,
                            entity_ids=(item.record_id,),
                            producer="raw_record_selector",
                        ),
                    ),
                    trust=TrustLevel.UNTRUSTED_DATA,
                    metadata={"role": item.role.value, "record_id": item.record_id},
                )
                for item in recent
            )
            recent_history = annotate_history(recent_entries, self.temporal_policy)
            recent_text = recent_history.text
        else:
            recent_text = "\n".join(f"{item.role.value}: {item.content}" for item in recent)
        recent_text = recent_text or "[]"
        overview_source_ids = (
            tuple(
                source_id
                for span in memory_overview.coverage_spans
                for source_id in (span.batch_id, *span.record_ids)
            )
            if isinstance(memory_overview, MemoryCoverageOverview)
            else ()
        )
        memory_segments = (
            self._untrusted_segment(
                "memory_overview",
                overview_text or "[]",
                overview_source_ids,
                overview_history.fragments if overview_history is not None else (),
                config_revision,
            ),
            self._untrusted_segment(
                "long_term_memories",
                long_text,
                long_source_ids,
                long_history.fragments if long_history is not None else (),
                config_revision,
            ),
            self._untrusted_segment(
                "recent_records",
                recent_text,
                tuple(item.record_id for item in recent),
                recent_history.fragments if recent_history is not None else (),
                config_revision,
            ),
        )
        if len(memory_segments[0].content) > limits.get("overview_chars", len(memory_segments[0].content)):
            raise BaiError("PROMPT_BUDGET_EXCEEDED", "记忆覆盖概览超过配置预算。")
        if len(memory_segments[1].content) > limits.get("long_term_chars", len(memory_segments[1].content)):
            raise BaiError("PROMPT_BUDGET_EXCEEDED", "长期记忆超过配置预算。")
        if len(memory_segments[2].content) > limits.get("recent_chars", len(memory_segments[2].content)):
            raise BaiError("PROMPT_BUDGET_EXCEEDED", "近期原文超过预算，必须先完成整理。")
        current_segment = (
            self._current_input_segment(current_input_record)
            if current_input_record is not None
            else PromptSegment(
                segment_id="current_input",
                trust=TrustLevel.USER_INSTRUCTION,
                content=current_input or "",
                source_ids=(turn_id,),
            )
        )
        segments.extend((*memory_segments, current_segment))
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

    def _untrusted_segment(
        self,
        segment_id: str,
        content: str,
        source_ids: tuple[str, ...],
        fragments,
        config_revision: str,
    ) -> PromptSegment:
        if self.boundary_renderer is None:
            return PromptSegment(
                segment_id=segment_id,
                trust=TrustLevel.UNTRUSTED_DATA,
                content=content,
                source_ids=source_ids,
                fragments=tuple(fragments),
            )
        if fragments:
            pieces = pieces_from_fragments(fragments)
        else:
            source_kind = SourceKind.DATA_FILE if segment_id != "current_input" else SourceKind.RUNTIME
            path = "data/memory/raw" if segment_id == "recent_records" else "data/memory/long_term.yaml"
            pieces = (
                PromptTextPiece(
                    piece_id=f"{segment_id}:body",
                    content=content,
                    sources=(
                        SourceRef(
                            source_kind=source_kind,
                            source_id=f"memory:{segment_id}",
                            project_relative_path=path if source_kind is SourceKind.DATA_FILE else None,
                            content_sha256=content_hash(content),
                            revision=config_revision,
                            entity_ids=source_ids,
                            producer="prompt_assembler",
                        ),
                    ),
                    trust=TrustLevel.UNTRUSTED_DATA,
                    entry_id=segment_id,
                ),
            )
        rendered = self.boundary_renderer.wrap(segment_id, pieces)
        return PromptSegment(
            segment_id=segment_id,
            trust=TrustLevel.UNTRUSTED_DATA,
            content=rendered.text,
            source_ids=source_ids,
            fragments=rendered.fragments,
        )

    def _current_input_segment(self, record: RawRecord) -> PromptSegment:
        source = SourceRef(
            source_kind=SourceKind.RUNTIME,
            source_id=f"runtime:current-input:{record.record_id}",
            content_sha256=record.content_sha256,
            revision=record.config_revision,
            entity_ids=(record.record_id, record.turn_id),
            producer="provisional_raw_record",
        )
        if self.temporal_policy is None:
            return PromptSegment(
                segment_id="current_input",
                trust=TrustLevel.USER_INSTRUCTION,
                content=record.content,
                source_ids=(record.record_id, record.turn_id),
            )
        annotated = annotate_history(
            (
                TemporalLogEntry(
                    entry_id=record.record_id,
                    body=record.content,
                    span=TemporalSpan(
                        start=record.created_at,
                        end=record.created_at,
                        kind=TemporalTimeKind.EVENT,
                    ),
                    sources=(source,),
                    trust=TrustLevel.USER_INSTRUCTION,
                    metadata={"role": record.role.value, "record_id": record.record_id},
                ),
            ),
            self.temporal_policy,
        )
        if self.boundary_renderer is None:
            return PromptSegment(
                segment_id="current_input",
                trust=TrustLevel.USER_INSTRUCTION,
                content=annotated.text,
                source_ids=(record.record_id, record.turn_id),
                fragments=annotated.fragments,
            )
        marker_pieces = tuple(
            PromptTextPiece(
                piece_id=fragment.fragment_id,
                content=fragment.content,
                sources=fragment.sources,
                trust=TrustLevel.TRUSTED_METADATA,
                entry_id=fragment.entry_id,
                kind=(
                    AnnotatedFragmentKind.TRUSTED_TIME_METADATA
                    if fragment.kind is AnnotatedFragmentKind.MARKER
                    else fragment.kind
                ),
            )
            for fragment in annotated.fragments
            if fragment.kind is not AnnotatedFragmentKind.BODY
        )
        body_fragment = next(
            fragment for fragment in annotated.fragments if fragment.kind is AnnotatedFragmentKind.BODY
        )
        bounded_body = self.boundary_renderer.wrap(
            "current_input",
            pieces_from_fragments((body_fragment,)),
        )
        positioned = position_pieces(
            (
                *marker_pieces,
                *pieces_from_fragments(bounded_body.fragments),
            )
        )
        return PromptSegment(
            segment_id="current_input",
            trust=TrustLevel.USER_INSTRUCTION,
            content=positioned.text,
            source_ids=(record.record_id, record.turn_id),
            fragments=positioned.fragments,
        )

    def request_parts(self, context: PromptContext) -> tuple[RequestPart, ...]:
        """[2026-07-20] part 来源来自加载/选择关系，绝不根据相同正文反向搜索。"""
        parts: list[RequestPart] = []
        order = 0
        for index, segment in enumerate(context.segments):
            if segment.fragments:
                for fragment in segment.fragments:
                    parts.append(
                        RequestPart(
                            part_id=f"message:{index}:{fragment.fragment_id}",
                            order=order,
                            participation=Participation.INCLUDED,
                            trust=fragment.trust,
                            payload_pointer=f"/messages/{index}/content",
                            text_span=(fragment.start, fragment.end),
                            content=fragment.content,
                            sources=fragment.sources,
                        )
                    )
                    order += 1
                continue
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
                    order=order,
                    participation=participation,
                    trust=segment.trust,
                    payload_pointer=f"/messages/{index}/content" if participation == Participation.INCLUDED else None,
                    text_span=(0, len(segment.content)) if participation == Participation.INCLUDED else None,
                    content=segment.content,
                    sources=sources if participation == Participation.INCLUDED else (),
                )
            )
            order += 1
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
