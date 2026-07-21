"""[2026-07-20] 记忆整理的三个历史变量独立标注且保持 canonical JSON 与来源。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from ruamel.yaml import YAML

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CompletionResult,
    ConfigAsset,
    CoverageSpan,
    CreatedBy,
    CurationCheckpoint,
    LongTermMemoryDocument,
    LongTermMemoryItem,
    MemoryCoverageOverview,
    MemoryKind,
    MemoryStatus,
    Role,
    SourceKind,
    SourceRef,
    SourceReference,
    SourceRelation,
    TemporalSegmentationPolicy,
    canonical_json,
    content_hash,
    thaw_json,
)
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.curation import CurationPolicy, CurationService
from bai_agent.memory.long_term import LongTermStore
from bai_agent.model_calls.provenance import validate_provenance
from bai_agent.prompting.boundaries import UntrustedBoundaryRenderer
from bai_agent.providers.deepseek import DeepSeekProvider


REVISION = "sha256:" + "1" * 64
BASE = datetime(2026, 7, 20, tzinfo=timezone.utc)


class CapturingGateway:
    is_model_call_gateway = True

    def __init__(self) -> None:
        self.calls = []

    async def complete(self, draft):
        self.calls.append(draft)
        return CompletionResult(
            text=json.dumps(
                {
                    "memory_candidates": [],
                    "overview": "新概览",
                },
                ensure_ascii=False,
            ),
            finish_reason="stop",
        )


def _policy() -> TemporalSegmentationPolicy:
    return TemporalSegmentationPolicy(
        display_timezone=ZoneInfo("Asia/Shanghai"),
        display_timezone_name="Asia/Shanghai",
        long_gap=timedelta(minutes=30),
        continuous_refresh=timedelta(minutes=120),
        split_on_local_date_change=True,
        config_source=SourceRef(
            source_kind=SourceKind.CONFIG_FILE,
            source_id="config:history_timestamps",
            project_relative_path="config/history_timestamps.toml",
            content_sha256="sha256:" + "2" * 64,
            revision=REVISION,
            producer="config_loader",
        ),
    )


@pytest.mark.asyncio
async def test_batch_existing_and_overview_are_independent_blocks_with_precise_prompt_spans(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path / "memory")
    for index, minute in enumerate((0, 5, 65, 60), start=1):
        archive.append(
            role=Role.USER if index % 2 else Role.ASSISTANT,
            content="重复正文" if index in {1, 3} else f"正文-{index}",
            turn_id=f"turn-00000000-0000-4000-8000-{(index + 1) // 2:012d}",
            state_id="default",
            config_revision=REVISION,
            record_id=f"rec-00000000-0000-4000-8000-{index:012d}",
            created_at=BASE + timedelta(minutes=minute),
        )
    records = archive.read_all()
    store = LongTermStore(tmp_path / "memory", archive)
    store.initialize()
    first_span = CoverageSpan(
        start_sequence=1,
        end_sequence=2,
        batch_id="batch-00000000-0000-4000-8000-000000000001",
        record_ids=tuple(item.record_id for item in records[:2]),
        record_hashes=tuple(item.content_sha256 for item in records[:2]),
    )
    existing = LongTermMemoryItem(
        memory_id="mem-00000000-0000-4000-8000-000000000001",
        kind=MemoryKind.USER,
        text="重复正文",
        status=MemoryStatus.ACTIVE,
        source_refs=(
            SourceReference(
                record_id=records[0].record_id,
                relation=SourceRelation.SUPPORTS,
                record_sha256=records[0].content_sha256,
            ),
        ),
        created_by=CreatedBy.MEMORY_CURATOR,
        created_at=BASE,
        updated_at=BASE,
    )
    later_existing = LongTermMemoryItem(
        memory_id="mem-00000000-0000-4000-8000-000000000002",
        kind=MemoryKind.SELF,
        text="晚期记忆",
        status=MemoryStatus.ACTIVE,
        source_refs=(
            SourceReference(
                record_id=records[1].record_id,
                relation=SourceRelation.SUPPORTS,
                record_sha256=records[1].content_sha256,
            ),
        ),
        created_by=CreatedBy.MEMORY_CURATOR,
        created_at=BASE,
        updated_at=BASE,
    )
    store.commit(
        LongTermMemoryDocument(
            schema_version=1,
            revision=1,
            curation=CurationCheckpoint(
                curated_through_sequence=2,
                last_batch_id=first_span.batch_id,
                updated_at=records[1].created_at,
                covered_record_ids=first_span.record_ids,
            ),
            coverage_overview=MemoryCoverageOverview(revision=1, text="旧概览", coverage_spans=(first_span,)),
            memories=(later_existing, existing),
        )
    )
    gateway = CapturingGateway()
    template = "BATCH\n$batch_records\nEXISTING\n$existing_memories\nOVERVIEW\n$current_overview\nPERSONA\n$curator_persona\nBOUNDARY\n$untrusted_boundary\nSCHEMA\n$output_schema"
    boundary_text = Path("config/prompts/untrusted_memory_boundary.md").read_text(encoding="utf-8")
    renderer = UntrustedBoundaryRenderer(
        ConfigAsset(
            asset_id="prompt:untrusted_memory_boundary",
            kind="prompt_template",
            project_relative_path="prompts/untrusted_memory_boundary.md",
            content=boundary_text,
            content_sha256=content_hash(boundary_text),
            revision=REVISION,
        )
    )
    service = CurationService(
        archive,
        store,
        gateway,
        CurationPolicy(max_records=2, reserved_records=0, min_batch_records=2, max_batch_records=2),
        curator_persona="整理人格",
        prompt_template=template,
        config_revision=REVISION,
        temporal_policy=_policy(),
        boundary_renderer=renderer,
    )
    proposal = await service.propose(force=True)
    assert proposal is not None
    draft = gateway.calls[0]
    prompt = draft.request.messages[1].content
    for block_name in ("batch_records", "existing_memories", "current_overview"):
        assert prompt.count(f"[UNTRUSTED {block_name}#") == 1
        assert prompt.count(f"[/UNTRUSTED {block_name}#") == 1
    assert renderer.instruction_text in draft.request.messages[0].content
    assert prompt.count("[时间：") >= 1
    assert prompt.count("[时间范围：") >= 2
    assert "SCHEMA\n[时间" not in prompt
    assert "memory_curation_v2 严格输出契约" in prompt
    assert "JSON Schema" not in prompt
    assert "user、rule、self、event、else" in prompt
    assert "r1" in prompt and "r2" in prompt
    batch_bodies = tuple(
        canonical_json(
            {
                "source_alias": f"r{index}",
                "time": item.created_at.isoformat(),
                "role": item.role.value,
                "text": item.content,
            }
        )
        for index, item in enumerate(sorted(records[2:], key=lambda value: (value.created_at, value.global_sequence)), start=1)
    )
    memory_body = canonical_json(
        {
            "kind": "user",
            "source_time": {"start": records[0].created_at.isoformat(), "end": records[0].created_at.isoformat()},
            "status": "active",
            "text": "重复正文",
        }
    )
    later_memory_body = canonical_json(
        {
            "kind": "self",
            "source_time": {"start": records[1].created_at.isoformat(), "end": records[1].created_at.isoformat()},
            "status": "active",
            "text": "晚期记忆",
        }
    )
    overview_body = canonical_json(
        {
            "coverage": {
                "end": records[1].created_at.isoformat(),
                "record_count": 2,
                "start": records[0].created_at.isoformat(),
            },
            "text": "旧概览",
        }
    )
    assert all(body in prompt for body in (*batch_bodies, memory_body, later_memory_body, overview_body))
    assert "[时间" not in memory_body
    assert json.loads(memory_body)["text"] == "重复正文"
    assert prompt.index(memory_body) < prompt.index(later_memory_body)
    assert prompt.index(batch_bodies[0]) < prompt.index(batch_bodies[1])
    for item in records:
        assert item.record_id not in prompt
        assert item.content_sha256 not in prompt
    for forbidden in (
        "coverage_spans", "record_hashes", "record_ids", "global_sequence",
        "config_revision", "memory_id", "batch_id", '"revision"', "source_refs",
    ):
        assert forbidden not in prompt
    included = tuple(part for part in draft.parts if part.payload_pointer == "/messages/1/content")
    assert len(included) > 3
    for part in included:
        assert prompt[part.text_span[0] : part.text_span[1]] == part.content
    for left, right in zip(sorted(included, key=lambda item: item.text_span), sorted(included, key=lambda item: item.text_span)[1:], strict=False):
        assert left.text_span[1] <= right.text_span[0]
    duplicate_parts = tuple(part for part in included if "重复正文" in part.content)
    assert len(duplicate_parts) == 2
    assert duplicate_parts[0].text_span != duplicate_parts[1].text_span
    schema_part = next(part for part in included if part.part_id.endswith(":output_schema"))
    assert schema_part.trust.value == "trusted_instruction"
    assert len(schema_part.sources) == 1
    assert schema_part.sources[0].source_kind == SourceKind.GENERATED
    assert schema_part.sources[0].producer == "curation_schema_renderer"
    assert schema_part.sources[0].content_sha256 == content_hash(schema_part.content)
    assert schema_part.sources[0].entity_ids == (
        proposal.batch.batch_id,
        *proposal.batch.record_ids,
    )
    assert draft.request.metadata == {}
    provider = DeepSeekProvider(SimpleNamespace(), {"model": "m", "stream": False})
    prepared = provider.prepare(draft.model_copy(update={"call_sequence": 1}), 1)
    payload = provider.materialize_sdk_kwargs(prepared)
    materialized = thaw_json(payload.sdk_kwargs)
    validate_provenance(materialized, prepared.parts)
    assert "[UNTRUSTED batch_records#" in materialized["messages"][1]["content"]
    assert "memory_curation_v2 严格输出契约" in materialized["messages"][1]["content"]
    provider_text = canonical_json(materialized)
    for item in records:
        assert item.record_id not in provider_text
        assert item.content_sha256 not in provider_text
    assert proposal.batch.batch_id not in provider_text
    assert existing.memory_id not in provider_text
    assert later_existing.memory_id not in provider_text
    for forbidden in (
        "coverage_spans", "record_hashes", "record_ids", "global_sequence",
        "config_revision", "memory_id", "batch_id", '"revision"', "source_refs",
    ):
        assert forbidden not in provider_text


@pytest.mark.asyncio
async def test_broken_memory_source_stops_curation_before_provider(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path / "memory")
    for index in range(1, 3):
        archive.append(
            role=Role.USER if index == 1 else Role.ASSISTANT,
            content=f"正文-{index}",
            turn_id="turn-00000000-0000-4000-8000-000000000001",
            state_id="default",
            config_revision=REVISION,
            record_id=f"rec-00000000-0000-4000-8000-{index:012d}",
            created_at=BASE + timedelta(minutes=index),
        )
    record = archive.read_all()[0]
    store = LongTermStore(tmp_path / "memory", archive)
    document = store.initialize()
    payload = document.model_dump(mode="json")
    payload["memories"] = [
        {
            "memory_id": "mem-00000000-0000-4000-8000-000000000001",
            "kind": "fact",
            "text": "损坏来源",
            "status": "active",
            "source_refs": [
                {
                    "record_id": record.record_id,
                    "relation": "supports",
                    "record_sha256": "sha256:" + "0" * 64,
                }
            ],
            "created_by": "manual",
            "created_at": BASE.isoformat(),
            "updated_at": BASE.isoformat(),
            "supersedes": [],
            "tags": [],
        }
    ]
    output = StringIO()
    YAML().dump(payload, output)
    damaged = output.getvalue()
    store.path.write_text(damaged, encoding="utf-8")
    store.last_valid_path.write_text(damaged, encoding="utf-8")
    gateway = CapturingGateway()
    service = CurationService(
        archive,
        store,
        gateway,
        CurationPolicy(max_records=2, reserved_records=0, min_batch_records=2, max_batch_records=2),
        curator_persona="整理人格",
        prompt_template="$batch_records",
        config_revision=REVISION,
        temporal_policy=_policy(),
    )
    with pytest.raises(BaiError) as raised:
        await service.propose(force=True)
    assert raised.value.code == "SOURCE_HASH_MISMATCH"
    assert gateway.calls == []
