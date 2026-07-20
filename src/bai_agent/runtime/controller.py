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
    canonical_json,
    new_id,
    utc_now,
)
from bai_agent.memory.transaction import PreTurnCheckpoint, TurnUnitOfWork
from bai_agent.memory.selection import select_long_term


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

    async def _complete(self, request: CompletionRequest, parts: tuple[RequestPart, ...], *, sequence: int, purpose: str, state_id: str, config_revision: str):
        if getattr(self.provider, "is_model_call_gateway", False):
            draft = ModelCallDraft(
                call_id=new_id("call"),
                turn_id=request.turn_id,
                flow_id=request.flow_id,
                call_sequence=sequence,
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
                    created_at=utc_now(),
                    state_id=resolution.state_id,
                    config_revision=config_revision,
                )
                uow = TurnUnitOfWork(self.transaction_root, self.repository, self.long_term_store)
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
                        call_sequence=1,
                    )
                else:
                    await self.curation_service.curate_if_needed()
            except TurnRejected:
                if uow is not None:
                    uow.discard()
                raise
            except BaiError as exc:
                if uow is not None and (exc.retryable or exc.code.startswith(("PROVIDER_", "NETWORK_"))):
                    uow.pending(exc.code)
                    uow.commit()
                raise
        all_records = self.repository.read_all()
        if self.long_term_store:
            long_document = self.long_term_store.load()
            recent = tuple(
                item
                for item in all_records
                if item.global_sequence > long_document.curation.curated_through_sequence
            )
            selected_memories = select_long_term(
                long_document.memories,
                content,
                max_chars=self.memory_budgets.get("long_term_chars", 16_384),
            )
            memory_overview = long_document.coverage_overview
            long_term_texts = tuple(item.text for item in selected_memories)
            long_term_source_ids = tuple(item.memory_id for item in selected_memories)
            curated_through = long_document.curation.curated_through_sequence
            coverage_records = all_records
        else:
            recent = tuple(item for item in all_records if item.record_id != user_record.record_id)
            memory_overview = "[]"
            long_term_texts = ()
            long_term_source_ids = ()
            curated_through = 0
            coverage_records = None
        context = self.prompt_assembler.assemble(
            flow_id=new_id("flow"),
            turn_id=resolved_turn_id,
            config_revision=config_revision,
            state_resolution=resolution,
            memory_overview=memory_overview,
            long_term_memories=long_term_texts,
            long_term_source_ids=long_term_source_ids,
            recent_records=recent,
            current_input=content,
            all_raw_records=coverage_records,
            curated_through=curated_through,
            budgets=self.memory_budgets,
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
        call_sequence = 2 if curation_proposal is not None else 1
        try:
            result = await self._complete(
                request, parts, sequence=call_sequence, purpose="chat",
                state_id=resolution.state_id, config_revision=config_revision,
            )
            seen_call_ids: set[str] = set()
            for _ in range(self.max_tool_rounds):
                if not result.tool_calls:
                    break
                if self.tool_executor is None:
                    raise BaiError("TOOL_DISABLED", "模型请求了未启用的工具。")
                tool_messages = []
                for raw_call in result.tool_calls:
                    call = ToolCall.model_validate(raw_call)
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
                    tool_messages.append(
                        Message(
                            role="tool",
                            content=canonical_json(tool_result.model_dump(mode="json")),
                            trust=TrustLevel.UNTRUSTED_DATA,
                            tool_call_id=call.call_id,
                        )
                    )
                request = request.model_copy(update={"messages": (*request.messages, *tool_messages)})
                for message in tool_messages:
                    index = len(request.messages) - len(tool_messages) + tool_messages.index(message)
                    parts = (
                        *parts,
                        RequestPart(
                            part_id=f"tool-result:{message.tool_call_id}",
                            order=len(parts),
                            participation=Participation.INCLUDED,
                            trust=message.trust,
                            payload_pointer=f"/messages/{index}/content",
                            text_span=(0, len(message.content)),
                            content=message.content,
                            sources=(
                                SourceRef(
                                    source_kind=SourceKind.RUNTIME,
                                    source_id=f"tool-result:{message.tool_call_id}",
                                    entity_ids=(message.tool_call_id or "unknown",),
                                    producer="tool_executor",
                                ),
                            ),
                        ),
                    )
                call_sequence += 1
                result = await self._complete(
                    request, parts, sequence=call_sequence, purpose="tool_continuation",
                    state_id=resolution.state_id, config_revision=config_revision,
                )
            if result.tool_calls:
                raise BaiError("TOOL_ROUND_LIMIT", "工具调用超过配置轮数限制。")
        except asyncio.CancelledError:
            raise
        except TurnRejected:
            if uow is not None:
                uow.discard()
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
                created_at=utc_now(),
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
        return assistant.content
