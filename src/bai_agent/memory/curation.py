"""[2026-07-19] 整理只在窗口边界运行一次模型调用，并在本地校验后联合提交。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
from string import Template
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    AnnotatedHistory,
    CompletionRequest,
    ConfigAsset,
    CreatedBy,
    CoverageSpan,
    CurationBatch,
    CurationProposal,
    CurationCheckpoint,
    LongTermMemoryDocument,
    LongTermMemoryItem,
    MemoryCoverageOverview,
    MemoryKind,
    MemoryStatus,
    Message,
    ModelCallDraft,
    Participation,
    RequestPart,
    SourceKind,
    SourceRef,
    SourceReference,
    SourceRelation,
    TrustLevel,
    TemporalLogEntry,
    TemporalSegmentationPolicy,
    canonical_json,
    content_hash,
)
from bai_agent.memory.temporal import MemoryTemporalProjector
from bai_agent.prompting.boundaries import (
    PromptTextPiece,
    UntrustedBoundaryRenderer,
    source_from_asset,
)
from bai_agent.prompting.temporal import annotate_history


@dataclass(frozen=True, slots=True)
class ProposedCuration:
    """[2026-07-20] 整理提案只携带目标与来源，整轮确认前不得写入长期记忆。"""

    target_document: LongTermMemoryDocument
    source_record_ids: tuple[str, ...]
    batch: CurationBatch | None = None


@dataclass(frozen=True, slots=True)
class _PromptPiece:
    """[2026-07-20] 模板展开时同步携带最终正文片段，避免从重复文本反查位置。"""

    piece_id: str
    content: str
    sources: tuple[SourceRef, ...]
    trust: TrustLevel


CURATION_OUTPUT_SCHEMA_ID = "memory_curation_v1"


def _render_output_contract(batch: CurationBatch) -> str:
    """Render the model-visible schema plus the batch-specific coverage constraints."""

    schema = canonical_json(CurationProposal.model_json_schema())
    record_ids = canonical_json(list(batch.record_ids))
    empty_example = canonical_json(
        {
            "memory_candidates": [],
            "overview_update": {
                "text": "本批次没有需要新增的长期记忆。",
                "record_ids": list(batch.record_ids),
            },
        }
    )
    return "\n".join(
        (
            f"{CURATION_OUTPUT_SCHEMA_ID} 严格输出契约：",
            "只输出一个 JSON 对象；不要输出 Markdown、代码围栏、注释或解释。",
            f"JSON Schema：{schema}",
            "附加业务约束：",
            f"- overview_update.record_ids 必须逐项、按原顺序完全等于 {record_ids}。",
            "- 每个 memory_candidates[*].source_record_ids 必须非空，且只能引用上述 record_id。",
            "- kind 只能是 fact、preference、constraint、event、task 之一；不得翻译这些枚举值。",
            "- 没有值得长期保留的内容时，memory_candidates 必须是空数组，但 overview_update 仍必须覆盖整个批次。",
            f"空候选时的最小合法示例：{empty_example}",
        )
    )


class CurationPolicy:
    def __init__(
        self,
        *,
        max_records: int,
        reserved_records: int,
        min_batch_records: int,
        max_batch_records: int,
    ) -> None:
        self.max_records = max_records
        self.reserved_records = reserved_records
        self.min_batch_records = min_batch_records
        self.max_batch_records = max_batch_records

    def next_batch(
        self,
        records,
        *,
        curated_through: int,
        config_revision: str,
        force: bool = False,
    ) -> CurationBatch | None:
        uncurated = [item for item in records if item.global_sequence > curated_through]
        direct_limit = self.max_records - self.reserved_records
        if not force and len(uncurated) <= direct_limit:
            return None
        grouped: OrderedDict[str, list] = OrderedDict()
        for record in uncurated:
            grouped.setdefault(record.turn_id, []).append(record)
        selected = []
        for turn_records in grouped.values():
            roles = {item.role.value for item in turn_records}
            if roles != {"user", "assistant"}:
                break
            if len(selected) + len(turn_records) > self.max_batch_records:
                break
            selected.extend(turn_records)
            if len(selected) >= self.min_batch_records and len(selected) >= max(1, len(uncurated) - direct_limit):
                break
        if len(selected) < self.min_batch_records:
            return None
        identity = "|".join(f"{item.record_id}:{item.content_sha256}" for item in selected)
        batch_uuid = uuid5(NAMESPACE_URL, identity)
        return CurationBatch(
            batch_id=f"batch-{batch_uuid}",
            old_frontier=curated_through,
            new_frontier=selected[-1].global_sequence,
            record_ids=tuple(item.record_id for item in selected),
            config_revision=config_revision,
            content_sha256=content_hash(identity),
        )

    @staticmethod
    def committed_frontier(old: int, *, proposed: int, committed: bool) -> int:
        return proposed if committed else old


class CurationService:
    def __init__(
        self,
        archive,
        store,
        provider,
        policy: CurationPolicy,
        *,
        curator_persona: str,
        prompt_template: str,
        config_revision: str,
        temporal_policy: TemporalSegmentationPolicy | None = None,
        boundary_renderer: UntrustedBoundaryRenderer | None = None,
        curator_asset: ConfigAsset | None = None,
        prompt_asset: ConfigAsset | None = None,
        max_attempts: int = 1,
        tracer=None,
    ) -> None:
        self.archive = archive
        self.store = store
        self.provider = provider
        self.policy = policy
        self.curator_persona = curator_persona
        self.prompt_template = prompt_template
        self.config_revision = config_revision
        self.temporal_policy = temporal_policy
        self.boundary_renderer = boundary_renderer
        self.curator_asset = curator_asset
        self.prompt_asset = prompt_asset
        self.max_attempts = max(1, max_attempts)
        self.tracer = tracer

    async def propose(
        self,
        *,
        force: bool = False,
        turn_id: str | None = None,
        call_sequence: int = 1,
    ) -> ProposedCuration | None:
        records = self.archive.read_all()
        document = self.store.load(raw_records=records)
        batch = self.policy.next_batch(
            records,
            curated_through=document.curation.curated_through_sequence,
            config_revision=self.config_revision,
            force=force,
        )
        if batch is None:
            return None
        indexed = {item.record_id: item for item in records}
        batch_records = tuple(indexed[item] for item in batch.record_ids)
        projector = MemoryTemporalProjector.from_records(records)
        batch_entries = tuple(
            projector.project_raw(
                item,
                body=canonical_json(
                    {
                        "record_id": item.record_id,
                        "global_sequence": item.global_sequence,
                        "role": item.role.value,
                        "content": item.content,
                        "content_sha256": item.content_sha256,
                    }
                ),
            )
            for item in batch_records
        )
        memory_entries = tuple(
            projector.project_memory(item, body=canonical_json(item.model_dump(mode="json")))
            for item in document.memories
        )
        overview_body = canonical_json(document.coverage_overview.model_dump(mode="json"))
        overview_entry = projector.project_overview(document.coverage_overview, body=overview_body)

        prompt_source = (
            source_from_asset(self.prompt_asset)
            if self.prompt_asset is not None
            else SourceRef(
                source_kind=SourceKind.CONFIG_FILE,
                source_id="prompt:memory_curation",
                project_relative_path="config/prompts/memory_curation.md",
                content_sha256=content_hash(self.prompt_template),
                revision=self.config_revision,
                producer="config_loader",
            )
        )
        persona_source = (
            source_from_asset(self.curator_asset)
            if self.curator_asset is not None
            else SourceRef(
                source_kind=SourceKind.CONFIG_FILE,
                source_id="persona:memory_curator",
                project_relative_path="config/personas/memory_curator.md",
                content_sha256=content_hash(self.curator_persona),
                revision=self.config_revision,
                producer="config_loader",
            )
        )
        batch_source = SourceRef(
            source_kind=SourceKind.DATA_FILE,
            source_id=f"curation-batch:{batch.batch_id}",
            project_relative_path="data/memory/raw",
            content_sha256=batch.content_sha256,
            revision=projector.raw_revision,
            entity_ids=batch.record_ids,
            producer="curation_policy",
        )
        long_term_source = SourceRef(
            source_kind=SourceKind.DATA_FILE,
            source_id=f"memory:document:{document.revision}",
            project_relative_path="data/memory/long_term.yaml",
            content_sha256=content_hash(canonical_json(document.model_dump(mode="json"))),
            revision=str(document.revision),
            entity_ids=tuple(item.memory_id for item in document.memories),
            producer="long_term_store",
        )
        output_contract = _render_output_contract(batch)
        output_contract_source = SourceRef(
            source_kind=SourceKind.GENERATED,
            source_id=f"generated:curation-output-contract:{batch.batch_id}",
            content_sha256=content_hash(output_contract),
            revision=self.config_revision,
            entity_ids=(batch.batch_id, *batch.record_ids),
            producer="curation_schema_renderer",
        )

        block_values = {
            "batch_records": self._history_pieces("batch_records", batch_entries),
            "existing_memories": self._history_pieces(
                "existing_memories", memory_entries, empty_sources=(long_term_source,)
            ),
            "current_overview": self._history_pieces(
                "current_overview",
                (overview_entry,) if overview_entry is not None else (),
                empty_text=overview_body,
                empty_sources=(long_term_source,),
            ),
        }
        if self.boundary_renderer is not None:
            block_values = {
                name: self._bounded_pieces(name, pieces)
                for name, pieces in block_values.items()
            }
        scalar_values = {
            "batch_metadata": _PromptPiece(
                "batch_metadata", canonical_json(batch.model_dump(mode="json")), (batch_source,),
                TrustLevel.UNTRUSTED_DATA,
            ),
            "curator_persona": _PromptPiece(
                "curator_persona", self.curator_persona, (persona_source,),
                TrustLevel.TRUSTED_INSTRUCTION,
            ),
            "untrusted_boundary": _PromptPiece(
                "untrusted_boundary",
                (
                    self.boundary_renderer.instruction_text
                    if self.boundary_renderer is not None
                    else "untrusted_data"
                ),
                (
                    (self.boundary_renderer.config_source,)
                    if self.boundary_renderer is not None
                    else (prompt_source,)
                ),
                TrustLevel.TRUSTED_INSTRUCTION,
            ),
            "output_schema": _PromptPiece(
                "output_schema", output_contract, (output_contract_source,),
                TrustLevel.TRUSTED_INSTRUCTION,
            ),
        }
        if self.boundary_renderer is not None:
            bounded_metadata = self._bounded_pieces(
                "batch_metadata", (scalar_values["batch_metadata"],)
            )
        else:
            bounded_metadata = (scalar_values["batch_metadata"],)
        try:
            prompt, prompt_pieces = self._expand_prompt(
                {
                    **block_values,
                    **{
                        name: (piece,)
                        for name, piece in scalar_values.items()
                        if name != "batch_metadata"
                    },
                    "batch_metadata": bounded_metadata,
                },
                template_source=prompt_source,
            )
        except (KeyError, ValueError) as exc:
            raise BaiError("PROMPT_TEMPLATE_INVALID", "记忆整理模板变量无效。") from exc
        resolved_turn_id = turn_id or f"curation-{batch.batch_id}"
        system_rendered = (
            self.boundary_renderer.compose_system_instruction(
                self.curator_persona,
                persona_source,
                composition_id="curation-system",
            )
            if self.boundary_renderer is not None
            else None
        )
        system_content = system_rendered.text if system_rendered is not None else self.curator_persona
        request = CompletionRequest(
            flow_id=f"curation-{batch.batch_id}",
            turn_id=resolved_turn_id,
            model_profile_id="memory_curator",
            messages=(
                Message(role="system", content=system_content, trust=TrustLevel.TRUSTED_INSTRUCTION),
                Message(role="user", content=prompt, trust=TrustLevel.UNTRUSTED_DATA),
            ),
            metadata={"batch_id": batch.batch_id, "record_ids": list(batch.record_ids)},
        )
        last_error: Exception | None = None
        proposal = None
        attempts = 1 if getattr(self.provider, "is_model_call_gateway", False) else self.max_attempts
        for _ in range(attempts):
            try:
                if getattr(self.provider, "is_model_call_gateway", False):
                    system_parts = (
                        tuple(
                            RequestPart(
                                part_id=f"curation:{batch.batch_id}:system:{fragment.fragment_id}",
                                order=index,
                                participation=Participation.INCLUDED,
                                trust=fragment.trust,
                                payload_pointer="/messages/0/content",
                                text_span=(fragment.start, fragment.end),
                                content=fragment.content,
                                sources=fragment.sources,
                            )
                            for index, fragment in enumerate(system_rendered.fragments)
                        )
                        if system_rendered is not None
                        else (
                            RequestPart(
                                part_id=f"curation:{batch.batch_id}:system", order=0,
                                participation=Participation.INCLUDED,
                                trust=TrustLevel.TRUSTED_INSTRUCTION,
                                payload_pointer="/messages/0/content", text_span=(0, len(self.curator_persona)),
                                content=self.curator_persona, sources=(persona_source,),
                            ),
                        )
                    )
                    parts = (
                        *system_parts,
                        *(
                            RequestPart(
                                part_id=f"curation:{batch.batch_id}:user:{index}:{piece.piece_id}",
                                order=len(system_parts) + index - 1,
                                participation=Participation.INCLUDED,
                                trust=piece.trust,
                                payload_pointer="/messages/1/content",
                                text_span=(start, end),
                                content=piece.content,
                                sources=piece.sources,
                            )
                            for index, (piece, start, end) in enumerate(prompt_pieces, start=1)
                        ),
                    )
                    response = await self.provider.complete(
                        ModelCallDraft(
                            call_id=f"call-{batch.batch_id}", turn_id=resolved_turn_id,
                            flow_id=request.flow_id, call_sequence=0,
                            purpose="memory_curation", persona_id="memory_curator", state_id=None,
                            config_revision=self.config_revision, model_profile_id="memory_curator",
                            request=request, parts=parts,
                        )
                    )
                else:
                    response = await self.provider.complete(request)
                proposal = self._validate_response(response.text, batch)
                break
            except Exception as exc:
                last_error = exc
        if proposal is None:
            if isinstance(last_error, BaiError):
                raise last_error
            raise BaiError("CURATION_FAILED", "记忆整理失败，原记录仍保留在直接窗口。", retryable=True) from last_error
        target = self._merge_document(document, batch, batch_records, proposal)
        return ProposedCuration(
            target_document=target,
            source_record_ids=batch.record_ids,
            batch=batch,
        )

    def _bounded_pieces(
        self,
        block_name: str,
        pieces: tuple[_PromptPiece, ...],
    ) -> tuple[_PromptPiece, ...]:
        """[2026-07-21] 每个整理变量只加一对边界，同时保留内部逐项来源。"""

        if self.boundary_renderer is None:
            return pieces
        rendered = self.boundary_renderer.wrap(
            block_name,
            tuple(
                PromptTextPiece(
                    piece_id=piece.piece_id,
                    content=piece.content,
                    sources=piece.sources,
                    trust=piece.trust,
                    entry_id=piece.piece_id,
                )
                for piece in pieces
            ),
        )
        return tuple(
            _PromptPiece(
                fragment.fragment_id,
                fragment.content,
                fragment.sources,
                fragment.trust,
            )
            for fragment in rendered.fragments
        )

    def _history_pieces(
        self,
        block_name: str,
        entries: tuple[TemporalLogEntry, ...],
        *,
        empty_text: str = "[]",
        empty_sources: tuple[SourceRef, ...] = (),
    ) -> tuple[_PromptPiece, ...]:
        if not entries:
            return (
                _PromptPiece(
                    f"{block_name}:empty",
                    empty_text,
                    empty_sources,
                    TrustLevel.UNTRUSTED_DATA,
                ),
            )
        if self.temporal_policy is None:
            result: list[_PromptPiece] = []
            for index, entry in enumerate(entries):
                if index:
                    result.append(
                        _PromptPiece(
                            f"{block_name}:{entry.entry_id}:separator",
                            "\n",
                            entry.sources,
                            entry.trust,
                        )
                    )
                result.append(
                    _PromptPiece(
                        f"{block_name}:{entry.entry_id}:body",
                        entry.body,
                        entry.sources,
                        entry.trust,
                    )
                )
            return tuple(result)
        annotated: AnnotatedHistory = annotate_history(entries, self.temporal_policy)
        return tuple(
            _PromptPiece(
                f"{block_name}:{fragment.fragment_id}",
                fragment.content,
                fragment.sources,
                fragment.trust,
            )
            for fragment in annotated.fragments
        )

    def _expand_prompt(
        self,
        values: dict[str, tuple[_PromptPiece, ...]],
        *,
        template_source: SourceRef,
    ) -> tuple[str, tuple[tuple[_PromptPiece, int, int], ...]]:
        """[2026-07-20] 单次扫描 Template，并在追加字符时记录最终绝对 span。"""
        text_parts: list[str] = []
        positioned: list[tuple[_PromptPiece, int, int]] = []
        cursor = 0

        def append(piece: _PromptPiece) -> None:
            nonlocal cursor
            if not piece.content:
                return
            start = cursor
            text_parts.append(piece.content)
            cursor += len(piece.content)
            positioned.append((piece, start, cursor))

        template_cursor = 0
        for match_index, match in enumerate(Template.pattern.finditer(self.prompt_template)):
            literal = self.prompt_template[template_cursor : match.start()]
            append(
                _PromptPiece(
                    f"template:{match_index}:literal",
                    literal,
                    (template_source,),
                    TrustLevel.TRUSTED_INSTRUCTION,
                )
            )
            if match.group("escaped") is not None:
                append(
                    _PromptPiece(
                        f"template:{match_index}:escaped",
                        "$",
                        (template_source,),
                        TrustLevel.TRUSTED_INSTRUCTION,
                    )
                )
            elif match.group("named") is not None or match.group("braced") is not None:
                name = match.group("named") or match.group("braced")
                for piece in values[name]:
                    append(piece)
            else:
                raise ValueError("Invalid placeholder in string")
            template_cursor = match.end()
        append(
            _PromptPiece(
                "template:tail",
                self.prompt_template[template_cursor:],
                (template_source,),
                TrustLevel.TRUSTED_INSTRUCTION,
            )
        )
        return "".join(text_parts), tuple(positioned)

    def commit(self, proposal: ProposedCuration) -> LongTermMemoryDocument:
        """[2026-07-20] 该入口仅供 READY_TO_COMMIT 发布路径调用。"""
        committed = self.store.commit(proposal.target_document)
        if self.tracer:
            batch = proposal.batch
            self.tracer.emit(
                "memory.curation_committed",
                batch_id=batch.batch_id if batch else "batch-none",
                revision=committed.revision,
                overview_revision=committed.coverage_overview.revision,
                count=len(proposal.source_record_ids),
                covered_range=(str((batch.old_frontier + 1, batch.new_frontier)) if batch else "[]"),
            )
        return committed

    async def curate_if_needed(self, *, force: bool = False) -> LongTermMemoryDocument | None:
        """[2026-07-20] 兼容旧调用；可拒绝轮次必须改用 propose 并随事务发布。"""
        proposal = await self.propose(force=force)
        return self.commit(proposal) if proposal is not None else None

    def _validate_response(self, text: str, batch: CurationBatch) -> dict:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BaiError("CURATION_SCHEMA_INVALID", "记忆整理响应不是完整 JSON。", retryable=True) from exc
        try:
            proposal = CurationProposal.model_validate(payload)
        except ValidationError as exc:
            raise BaiError("CURATION_SCHEMA_INVALID", "记忆整理响应字段不符合 Schema。") from exc
        if proposal.overview_update.record_ids != batch.record_ids:
            raise BaiError("CURATION_COVERAGE_INVALID", "整理概览必须覆盖且仅覆盖当前批次。")
        allowed = set(batch.record_ids)
        for candidate in proposal.memory_candidates:
            sources = candidate.source_record_ids
            if not sources or not set(sources).issubset(allowed):
                raise BaiError("CURATION_SOURCE_INVALID", "长期记忆候选来源不属于当前批次。")
            self.store.guard.ensure_safe(candidate.text)
        self.store.guard.ensure_safe(proposal.overview_update.text)
        return proposal.model_dump(mode="python")

    def _merge_document(self, document, batch, records, proposal) -> LongTermMemoryDocument:
        by_id = {item.record_id: item for item in records}
        memories = list(document.memories)
        manual_texts = {
            item.text for item in memories if item.created_by == CreatedBy.MANUAL and item.status == MemoryStatus.ACTIVE
        }
        now = records[-1].created_at
        for index, candidate in enumerate(proposal["memory_candidates"]):
            if candidate["text"] in manual_texts:
                continue
            memory_uuid = uuid5(NAMESPACE_URL, f"{batch.batch_id}:{index}:{candidate['text']}")
            memories.append(
                LongTermMemoryItem(
                    memory_id=f"mem-{memory_uuid}",
                    kind=MemoryKind(candidate["kind"]),
                    text=candidate["text"],
                    status=MemoryStatus.ACTIVE,
                    source_refs=tuple(
                        SourceReference(
                            record_id=record_id,
                            relation=SourceRelation.SUPPORTS,
                            record_sha256=by_id[record_id].content_sha256,
                        )
                        for record_id in candidate["source_record_ids"]
                    ),
                    created_by=CreatedBy.MEMORY_CURATOR,
                    created_at=now,
                    updated_at=now,
                    supersedes=(),
                    tags=tuple(candidate["tags"]),
                )
            )
        revision = document.revision + 1
        span = CoverageSpan(
            start_sequence=batch.old_frontier + 1,
            end_sequence=batch.new_frontier,
            batch_id=batch.batch_id,
            record_ids=batch.record_ids,
            record_hashes=tuple(by_id[item].content_sha256 for item in batch.record_ids),
        )
        updated = LongTermMemoryDocument(
            schema_version=1,
            revision=revision,
            curation=CurationCheckpoint(
                curated_through_sequence=batch.new_frontier,
                last_batch_id=batch.batch_id,
                updated_at=now,
                covered_record_ids=batch.record_ids,
            ),
            coverage_overview=MemoryCoverageOverview(
                revision=revision,
                text=proposal["overview_update"]["text"],
                coverage_spans=(*document.coverage_overview.coverage_spans, span),
            ),
            memories=tuple(memories),
        )
        return updated
