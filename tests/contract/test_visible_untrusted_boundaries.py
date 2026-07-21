"""[2026-07-21] 可见边界、system 子来源与最终 provider payload 使用同一份正文。"""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from bai_agent.application import _temporal_policy
from bai_agent.config.loader import load_config
from bai_agent.domain.models import (
    CompletionRequest,
    Message,
    ModelCallDraft,
    RawRecord,
    Role,
    StateResolutionResult,
    TrustLevel,
    thaw_json,
)
from bai_agent.model_calls.estimation import DeepSeekCharacterEstimator
from bai_agent.model_calls.provenance import validate_provenance
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.prompting.boundaries import UntrustedBoundaryRenderer
from bai_agent.prompting.personas import PersonaPromptSet
from bai_agent.providers.deepseek import DeepSeekProvider


def test_system_sources_and_materialized_untrusted_boundaries_are_exact() -> None:
    snapshot = load_config(Path("config"), require_credentials=False)
    assets = {asset.asset_id: asset for asset in snapshot.assets}
    personas = PersonaPromptSet.from_snapshot(snapshot)
    renderer = UntrustedBoundaryRenderer(assets["prompt:untrusted_memory_boundary"])
    assembler = PromptAssembler.mvp(
        personas.chat,
        personas.state_prompts(("state_default",)),
        base_asset=assets["persona:chat"],
        state_assets=(assets["persona:state_default"],),
        temporal_policy=_temporal_policy(snapshot),
        boundary_renderer=renderer,
    )
    turn_id = "turn-00000000-0000-4000-8000-999999999999"
    current = RawRecord.create(
        record_id="rec-00000000-0000-4000-8000-999999999999",
        global_sequence=1,
        turn_id=turn_id,
        role=Role.USER,
        content="本轮问题",
        created_at=datetime(2026, 7, 20, 1, 2, tzinfo=timezone.utc),
        state_id="default",
        config_revision=snapshot.revision,
    )
    context = assembler.assemble(
        flow_id="flow-visible-boundary",
        turn_id=turn_id,
        config_revision=snapshot.revision,
        state_resolution=StateResolutionResult(
            state_id="default",
            ordered_persona_ids=("state_default",),
            resolver_id="static",
            resolver_version="1",
            reason_code="configured_default",
        ),
        memory_overview="[]",
        long_term_memories=(),
        recent_records=(),
        current_input_record=current,
    )
    request = CompletionRequest(
        flow_id=context.flow_id,
        turn_id=turn_id,
        model_profile_id="chat",
        messages=tuple(
            Message(
                role="system" if segment.trust is TrustLevel.TRUSTED_INSTRUCTION else "user",
                content=segment.content,
                trust=segment.trust,
            )
            for segment in context.segments
        ),
    )
    draft = ModelCallDraft(
        call_id="call-visible-boundary",
        turn_id=turn_id,
        flow_id=context.flow_id,
        call_sequence=1,
        purpose="chat",
        persona_id="chat",
        state_id="default",
        config_revision=snapshot.revision,
        model_profile_id="chat",
        request=request,
        parts=assembler.request_parts(context),
    )
    provider = DeepSeekProvider(
        SimpleNamespace(),
        {"model": "deepseek-v4-flash", "stream": False, "max_output_tokens": 128},
    )
    prepared = provider.prepare(draft, 1)
    payload = provider.materialize_sdk_kwargs(prepared)
    sdk = thaw_json(payload.sdk_kwargs)
    validate_provenance(sdk, prepared.parts)

    system_content = sdk["messages"][0]["content"]
    assert system_content == f"{personas.chat.rstrip()}\n\n{renderer.instruction_text}"
    system_parts = tuple(
        part for part in prepared.parts if part.payload_pointer == "/messages/0/content"
    )
    chat_part = next(part for part in system_parts if part.content == personas.chat.rstrip())
    boundary_part = next(part for part in system_parts if part.content == renderer.instruction_text)
    assert {source.source_id for source in chat_part.sources} == {"persona:chat"}
    assert {source.source_id for source in boundary_part.sources} == {
        "prompt:untrusted_memory_boundary"
    }
    assert chat_part.text_span[1] <= boundary_part.text_span[0]
    assert chat_part.sources[0].content_sha256 == assets["persona:chat"].content_sha256
    assert boundary_part.sources[0].content_sha256 == assets["prompt:untrusted_memory_boundary"].content_sha256
    assert boundary_part.sources[0].revision == snapshot.revision

    blocks = {
        "memory_overview": sdk["messages"][2]["content"],
        "long_term_memories": sdk["messages"][3]["content"],
        "recent_records": sdk["messages"][4]["content"],
    }
    for name, text in blocks.items():
        assert text.count(f"<<<UNTRUSTED_DATA_BEGIN block={name} id=") == 1
        assert text.count(f"<<<UNTRUSTED_DATA_END block={name} id=") == 1
    current_text = sdk["messages"][5]["content"]
    assert current_text.count("<<<UNTRUSTED_DATA_BEGIN block=current_input.timestamp id=") == 1
    assert current_text.count("<<<UNTRUSTED_DATA_END block=current_input.timestamp id=") == 1
    assert current_text.endswith("本轮问题")
    body = next(part for part in prepared.parts if part.content == "本轮问题")
    marker = next(part for part in prepared.parts if part.content.startswith("[时间：2026-07-20 09:02"))
    assert body.trust is TrustLevel.USER_INSTRUCTION
    assert marker.trust is TrustLevel.UNTRUSTED_DATA
    assert {source.source_id for source in marker.sources} >= {
        "config:history_timestamps",
        f"runtime:current-input:{current.record_id}",
    }

    estimate = DeepSeekCharacterEstimator(context_capacity=1_000_000).estimate(prepared, payload)
    assert estimate.status == "estimated"
    assert estimate.estimated_input_tokens == sum(estimate.part_tokens.values()) + estimate.protocol_overhead_tokens
    assert any("untrusted-boundary-open" in part_id for part_id in estimate.part_tokens)
