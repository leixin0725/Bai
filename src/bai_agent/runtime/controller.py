"""[2026-07-19] 单轮控制器强制输入先存、完整输出先存，并保留失败 pending turn。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import CompletionRequest, Message, Role, StateResolutionContext, TrustLevel, new_id


class SingleTurnController:
    def __init__(self, repository, provider, state_resolver, prompt_assembler, *, on_output: Callable[[str], None] | None = None, tracer=None) -> None:
        self.repository = repository
        self.provider = provider
        self.state_resolver = state_resolver
        self.prompt_assembler = prompt_assembler
        self.on_output = on_output
        self.tracer = tracer

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
        existing = [item for item in self.repository.read_all() if item.turn_id == resolved_turn_id]
        if existing:
            if not resume_pending or len(existing) != 1 or existing[0].role != Role.USER:
                raise BaiError("TURN_ALREADY_CONFIRMED", "轮次已存在或不是可恢复 pending 状态。")
            user_record = existing[0]
            content = user_record.content
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
        recent = tuple(item for item in self.repository.read_all() if item.record_id != user_record.record_id)
        context = self.prompt_assembler.assemble(
            flow_id=new_id("flow"),
            turn_id=resolved_turn_id,
            config_revision=config_revision,
            state_resolution=resolution,
            memory_overview="[]",
            long_term_memories=(),
            recent_records=recent,
            current_input=content,
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
        )
        try:
            result = await self.provider.complete(request)
        except asyncio.CancelledError:
            raise
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
        if self.on_output:
            self.on_output(assistant.content)
        return assistant.content
