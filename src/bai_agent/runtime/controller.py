"""[2026-07-19] 单轮控制器强制输入先存、完整输出先存，并保留失败 pending turn。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from bai_agent.domain.errors import BaiError, TurnRejected
from bai_agent.domain.models import (
    CompletionRequest,
    Message,
    ModelCallDraft,
    Participation,
    RequestPart,
    Role,
    RawRecord,
    StateResolutionContext,
    ToolCall,
    ToolExecutionContext,
    TrustLevel,
    SourceKind,
    SourceRef,
    TemporalLogEntry,
    TemporalSpan,
    TemporalTimeKind,
    ToolHistoryEvent,
    ToolHistoryEventKind,
    canonical_json,
    content_hash,
    new_id,
)
from bai_agent.memory.transaction import PreTurnCheckpoint, TurnUnitOfWork
from bai_agent.memory.selection import select_long_term
from bai_agent.memory.temporal import MemoryTemporalProjector
from bai_agent.prompting.boundaries import pieces_from_fragments
from bai_agent.prompting.temporal import annotate_history
from bai_agent.domain.ports import SystemClock


def render_tool_history(
    events: tuple[ToolHistoryEvent, ...],
    temporal_policy,
    *,
    message_offset: int,
    part_order: int,
    boundary_renderer=None,
) -> tuple[tuple[Message, ...], tuple[RequestPart, ...]]:
    """[2026-07-20] 从整轮未标注事件重建工具 block，并平移 fragment。"""
    entries = tuple(
        TemporalLogEntry(
            entry_id=event.event_id,
            body=event.original_body,
            span=TemporalSpan(
                start=event.occurred_at,
                end=event.occurred_at,
                kind=TemporalTimeKind.EVENT,
            ),
            sources=event.sources,
            trust=TrustLevel.UNTRUSTED_DATA,
            metadata={"kind": event.kind.value},
        )
        for event in events
    )
    annotated = annotate_history(entries, temporal_policy, separator="")
    fragments_by_entry = {
        event.event_id: tuple(
            fragment for fragment in annotated.fragments if fragment.entry_id == event.event_id
        )
        for event in events
    }
    messages: list[Message] = []
    parts: list[RequestPart] = []
    for event_index, event in enumerate(events):
        fragments = fragments_by_entry[event.event_id]
        if boundary_renderer is not None:
            rendered = boundary_renderer.wrap(
                f"tool_history.event.{event_index}",
                pieces_from_fragments(fragments),
            )
            content = rendered.text
            fragments = rendered.fragments
        else:
            content = "".join(fragment.content for fragment in fragments)
        tool_calls = tuple(call.model_dump(mode="python") for call in event.tool_calls)
        messages.append(
            Message(
                role=event.role,
                content=content,
                trust=TrustLevel.UNTRUSTED_DATA,
                tool_calls=tool_calls,
                tool_call_id=event.tool_call_id,
            )
        )
        pointer = f"/messages/{message_offset + event_index}/content"
        local_cursor = 0
        if fragments:
            for fragment in fragments:
                end = local_cursor + len(fragment.content)
                parts.append(
                    RequestPart(
                        part_id=f"tool-history:{event.event_id}:{fragment.fragment_id}",
                        order=part_order + len(parts),
                        participation=Participation.INCLUDED,
                        trust=fragment.trust,
                        payload_pointer=pointer,
                        text_span=(local_cursor, end),
                        content=fragment.content,
                        sources=fragment.sources,
                    )
                )
                local_cursor = end
        else:
            parts.append(
                RequestPart(
                    part_id=f"tool-history:{event.event_id}:empty-body",
                    order=part_order + len(parts),
                    participation=Participation.INCLUDED,
                    trust=TrustLevel.UNTRUSTED_DATA,
                    payload_pointer=pointer,
                    text_span=(0, 0),
                    content="",
                    sources=event.sources,
                )
            )
    return tuple(messages), tuple(parts)


class SingleTurnController:
    def __init__(
        self,
        repository,
        provider,
        state_resolver,
        prompt_assembler,
        *,
        on_output: Callable[[str], None] | None = None,
        tracer=None,
        long_term_store=None,
        curation_service=None,
        tool_executor=None,
        max_tool_rounds: int = 0,
        memory_budgets: dict[str, int] | None = None,
        tool_definitions: tuple[dict, ...] = (),
        transaction_root=None,
        temporal_policy=None,
        clock=None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.state_resolver = state_resolver
        self.prompt_assembler = prompt_assembler
        self.on_output = on_output
        self.tracer = tracer
        self.long_term_store = long_term_store
        self.curation_service = curation_service
        self.tool_executor = tool_executor
        self.max_tool_rounds = max_tool_rounds
        self.memory_budgets = memory_budgets or {}
        self.tool_definitions = tool_definitions
        self.transaction_root = transaction_root
        self.temporal_policy = temporal_policy or getattr(prompt_assembler, "temporal_policy", None)
        self.clock = clock or SystemClock()

    def discard_pending(self, *, expected_turn_id: str | None = None) -> str | None:
        """[2026-07-20] 在长期引用门禁通过后委托归档层原子放弃唯一尾部 pending。"""
        pending = self.repository.pending_turn()
        if pending is not None and self.long_term_store is not None:
            self.long_term_store.assert_pending_discardable(pending)
        return self.repository.discard_pending_tail(expected_turn_id=expected_turn_id)

    def _discard_rejected_turn(
        self, uow: TurnUnitOfWork | None, *, resumed: bool, turn_id: str
    ) -> None:
        if uow is not None:
            uow.discard()
        elif resumed:
            self.discard_pending(expected_turn_id=turn_id)

    async def _complete(self, request: CompletionRequest, parts: tuple[RequestPart, ...], *, purpose: str, state_id: str, config_revision: str):
        if getattr(self.provider, "is_model_call_gateway", False):
            draft = ModelCallDraft(
                call_id=new_id("call"),
                turn_id=request.turn_id,
                flow_id=request.flow_id,
                call_sequence=0,
                purpose=purpose,
                persona_id="chat",
                state_id=state_id,
                config_revision=config_revision,
                model_profile_id=request.model_profile_id or "chat",
                request=request,
                parts=parts,
            )
            return await self.provider.complete(draft)
        return await self.provider.complete(request)

    async def run_turn(
        self,
        content: str,
        *,
        turn_id: str | None = None,
        resume_pending: bool = False,
        config_revision: str = "sha256:" + "0" * 64,
    ) -> str:
        resolved_turn_id = turn_id or new_id("turn")
        resolution = self.state_resolver.resolve(
            StateResolutionContext(turn_id=resolved_turn_id, untrusted_text=content)
        )
        all_before = self.repository.read_all()
        existing = [item for item in all_before if item.turn_id == resolved_turn_id]
        uow = None
        if existing:
            if not resume_pending or len(existing) != 1 or existing[0].role != Role.USER:
                raise BaiError("TURN_ALREADY_CONFIRMED", "轮次已存在或不是可恢复 pending 状态。")
            user_record = existing[0]
            content = user_record.content
        else:
            if self.transaction_root is not None:
                user_record = RawRecord.create(
                    record_id=new_id("rec"),
                    global_sequence=len(all_before) + 1,
                    turn_id=resolved_turn_id,
                    role=Role.USER,
                    content=content,
                    created_at=self.clock.now(),
                    state_id=resolution.state_id,
                    config_revision=config_revision,
                )
                uow = TurnUnitOfWork(
                    self.transaction_root, self.repository, self.long_term_store,
                    tracer=self.tracer,
                )
                uow.begin(
                    PreTurnCheckpoint.capture(self.repository, self.long_term_store, resolution.state_id),
                    user_record,
                )
            else:
                user_record = self.repository.append(
                    role=Role.USER,
                    content=content,
                    turn_id=resolved_turn_id,
                    state_id=resolution.state_id,
                    config_revision=config_revision,
                    created_at=self.clock.now(),
                )
            if self.tracer:
                self.tracer.emit(
                    "turn.user_persisted",
                    record_id=user_record.record_id,
                    turn_id=resolved_turn_id,
                    state_id=resolution.state_id,
                    config_revision=config_revision,
                )
        curation_proposal = None
        if self.curation_service:
            try:
                if uow is not None and hasattr(self.curation_service, "propose"):
                    curation_proposal = await self.curation_service.propose(
                        turn_id=resolved_turn_id,
                    )
                else:
                    await self.curation_service.curate_if_needed()
            except TurnRejected:
                self._discard_rejected_turn(
                    uow, resumed=resume_pending, turn_id=resolved_turn_id
                )
                raise
            except BaiError as exc:
                if uow is not None and (exc.retryable or exc.code.startswith(("PROVIDER_", "NETWORK_"))):
                    uow.pending(exc.code)
                    uow.commit()
                raise
        all_records = self.repository.read_all()
        if self.long_term_store:
            long_document = self.long_term_store.load(raw_records=all_records)
            memory_projector = MemoryTemporalProjector.from_records(all_records)
            recent = tuple(
                item
                for item in all_records
                if item.global_sequence > long_document.curation.curated_through_sequence
                and item.record_id != user_record.record_id
            )
            selected_memories = select_long_term(
                long_document.memories,
                content,
                max_chars=self.memory_budgets.get("long_term_chars", 16_384),
                temporal_projector=memory_projector,
                temporal_policy=self.prompt_assembler.temporal_policy,
                boundary_renderer=self.prompt_assembler.boundary_renderer,
            )
            memory_overview = long_document.coverage_overview
            long_term_values = selected_memories
            long_term_source_ids = tuple(item.memory_id for item in selected_memories)
            curated_through = long_document.curation.curated_through_sequence
            coverage_records = all_records
        else:
            memory_projector = None
            recent = tuple(item for item in all_records if item.record_id != user_record.record_id)
            memory_overview = "[]"
            long_term_values = ()
            long_term_source_ids = ()
            curated_through = 0
            coverage_records = None
        context = self.prompt_assembler.assemble(
            flow_id=new_id("flow"),
            turn_id=resolved_turn_id,
            config_revision=config_revision,
            state_resolution=resolution,
            memory_overview=memory_overview,
            long_term_memories=long_term_values,
            long_term_source_ids=long_term_source_ids,
            recent_records=recent,
            current_input_record=user_record,
            all_raw_records=coverage_records,
            curated_through=curated_through,
            budgets=self.memory_budgets,
            memory_projector=memory_projector,
        )
        if self.tracer:
            self.tracer.emit(
                "prompt.ready",
                turn_id=resolved_turn_id,
                flow_id=context.flow_id,
                state_id=resolution.state_id,
                config_revision=config_revision,
                source_manifest=list(context.source_manifest),
                covered_range=str(context.coverage.get("covered_range", [])),
                direct_range=str(context.coverage.get("direct_range", [])),
            )
        request = CompletionRequest(
            flow_id=context.flow_id,
            turn_id=resolved_turn_id,
            messages=tuple(
                Message(
                    role="system" if item.trust == TrustLevel.TRUSTED_INSTRUCTION else "user",
                    content=item.content,
                    trust=item.trust,
                )
                for item in context.segments
            ),
            tool_definitions=self.tool_definitions,
            model_profile_id="chat",
        )
        parts = self.prompt_assembler.request_parts(context)
        base_request = request
        base_parts = parts
        tool_events: list[ToolHistoryEvent] = []
        try:
            result = await self._complete(
                request, parts, purpose="chat",
                state_id=resolution.state_id, config_revision=config_revision,
            )
            seen_call_ids: set[str] = set()
            for _ in range(self.max_tool_rounds):
                if not result.tool_calls:
                    break
                if self.tool_executor is None:
                    raise BaiError("TOOL_DISABLED", "模型请求了未启用的工具。")
                if result.accepted_at is None or result.origin_call_id is None:
                    raise BaiError("TOOL_EVENT_TIME_MISSING", "工具调用缺少已接受事件时间。")
                calls = tuple(ToolCall.model_validate(raw_call) for raw_call in result.tool_calls)
                call_source = SourceRef(
                    source_kind=SourceKind.RUNTIME,
                    source_id=f"model-response:{result.origin_call_id}",
                    content_sha256=content_hash(
                        canonical_json(
                            {
                                "text": result.text,
                                "tool_calls": [call.model_dump(mode="json") for call in calls],
                            }
                        )
                    ),
                    entity_ids=(result.origin_call_id, *(call.call_id for call in calls)),
                    producer="model_call_gateway",
                )
                tool_events.append(
                    ToolHistoryEvent(
                        event_id=f"tool-call-batch:{result.origin_call_id}",
                        kind=ToolHistoryEventKind.TOOL_CALL_BATCH,
                        occurred_at=result.accepted_at,
                        original_body=result.text,
                        role="assistant",
                        tool_calls=calls,
                        sources=(call_source,),
                    )
                )
                for call in calls:
                    if call.call_id in seen_call_ids:
                        raise BaiError("TOOL_CALL_DUPLICATE", "模型返回了重复工具调用 ID。")
                    seen_call_ids.add(call.call_id)
                    tool_result = await self.tool_executor.execute(
                        call,
                        ToolExecutionContext(
                            flow_id=context.flow_id,
                            turn_id=resolved_turn_id,
                            persona_id="chat",
                            state_id=resolution.state_id,
                            config_revision=config_revision,
                            trigger_record_id=user_record.record_id,
                        ),
                    )
                    if tool_result.completed_at is None or tool_result.origin_id is None:
                        raise BaiError("TOOL_EVENT_TIME_MISSING", "工具结果缺少可发送完成时间。")
                    result_body = canonical_json(tool_result.model_dump(mode="json"))
                    tool_events.append(
                        ToolHistoryEvent(
                            event_id=tool_result.origin_id,
                            kind=ToolHistoryEventKind.TOOL_RESULT,
                            occurred_at=tool_result.completed_at,
                            original_body=result_body,
                            role="tool",
                            tool_call_id=call.call_id,
                            sources=(
                                SourceRef(
                                    source_kind=SourceKind.RUNTIME,
                                    source_id=tool_result.origin_id,
                                    content_sha256=content_hash(result_body),
                                    entity_ids=(call.call_id, tool_result.origin_id),
                                    producer="tool_executor",
                                ),
                            ),
                        )
                    )
                if self.temporal_policy is None:
                    raise BaiError("TEMPORAL_POLICY_MISSING", "工具历史缺少统一时间策略。")
                tool_messages, tool_parts = render_tool_history(
                    tuple(tool_events),
                    self.temporal_policy,
                    message_offset=len(base_request.messages),
                    part_order=len(base_parts),
                    boundary_renderer=self.prompt_assembler.boundary_renderer,
                )
                request = base_request.model_copy(
                    update={"messages": (*base_request.messages, *tool_messages)}
                )
                parts = (*base_parts, *tool_parts)
                result = await self._complete(
                    request, parts, purpose="tool_continuation",
                    state_id=resolution.state_id, config_revision=config_revision,
                )
            if result.tool_calls:
                raise BaiError("TOOL_ROUND_LIMIT", "工具调用超过配置轮数限制。")
        except asyncio.CancelledError:
            raise
        except TurnRejected:
            self._discard_rejected_turn(
                uow, resumed=resume_pending, turn_id=resolved_turn_id
            )
            raise
        except BaiError as exc:
            if uow is not None and (exc.retryable or exc.code.startswith(("PROVIDER_", "NETWORK_"))):
                uow.pending(exc.code)
                uow.commit()
            raise
        if uow is not None:
            assistant = RawRecord.create(
                record_id=new_id("rec"),
                global_sequence=len(all_before) + 2,
                turn_id=resolved_turn_id,
                role=Role.ASSISTANT,
                content=result.text,
                created_at=self.clock.now(),
                state_id=resolution.state_id,
                config_revision=config_revision,
            )
            uow.ready(
                assistant,
                curation_proposal.target_document if curation_proposal is not None else None,
            )
            uow.commit()
            assistant = next(
                item for item in self.repository.read_all() if item.record_id == assistant.record_id
            )
        else:
            assistant = self.repository.append(
                role=Role.ASSISTANT,
                content=result.text,
                turn_id=resolved_turn_id,
                state_id=resolution.state_id,
                config_revision=config_revision,
                created_at=self.clock.now(),
            )
        if self.tracer:
            self.tracer.emit(
                "turn.assistant_persisted",
                record_id=assistant.record_id,
                turn_id=resolved_turn_id,
                flow_id=context.flow_id,
                state_id=resolution.state_id,
                config_revision=config_revision,
            )
            if self.long_term_store:
                self.tracer.emit(
                    "prompt.coverage",
                    turn_id=resolved_turn_id,
                    flow_id=context.flow_id,
                    overview_revision=self.long_term_store.load().revision,
                    covered_range=str(context.coverage.get("covered_range", [])),
                    direct_range=str(context.coverage.get("direct_range", [])),
                )
        if self.on_output:
            self.on_output(assistant.content)
            if getattr(self.provider, "debug_enabled", False):
                usage = getattr(self.provider, "last_actual_usage", None)
                if usage is not None and usage.status == "actual":
                    percent = f"{usage.actual_percent:.1f}%" if usage.actual_percent is not None else "未知"
                    self.on_output(
                        "实际用量："
                        f"输入={usage.actual_input_tokens}，输出={usage.actual_output_tokens}，"
                        f"总量={usage.actual_total_tokens}，占比={percent}，"
                        f"输入估算误差={usage.input_estimation_error if usage.input_estimation_error is not None else '未知'}。"
                    )
                elif usage is not None:
                    self.on_output(f"实际用量：不可用（{usage.reason}）")
        return assistant.content
