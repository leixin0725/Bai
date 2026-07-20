"""[2026-07-20] 记忆整理的三个历史变量独立标注且保持 canonical JSON 与来源。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bai_agent.domain.models import (
    CompletionResult,
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
)
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.curation import CurationPolicy, CurationService
from bai_agent.memory.long_term import LongTermStore


REVISION = "sha256:" + "1" * 64
BASE = datetime(2026, 7, 20, tzinfo=timezone.utc)


class CapturingGateway:
    is_model_call_gateway = True

    def __init__(self) -> None:
        self.calls = []

    async def complete(self, draft):
        self.calls.append(draft)
        record_ids = list(draft.request.metadata["record_ids"])
        return CompletionResult(
            text=json.dumps(
                {
                    "memory_candidates": [],
                    "overview_update": {"text": "新概览", "record_ids": record_ids},
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
    for index, minute in enumerate((0, 5, 60, 65), start=1):
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
        kind=MemoryKind.FACT,
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
            memories=(existing,),
        )
    )
    gateway = CapturingGateway()
    template = "BATCH\n$batch_records\nMETA\n$batch_metadata\nEXISTING\n$existing_memories\nOVERVIEW\n$current_overview\nPERSONA\n$curator_persona\nBOUNDARY\n$untrusted_boundary\nSCHEMA\n$output_schema"
    service = CurationService(
        archive,
        store,
        gateway,
        CurationPolicy(max_records=2, reserved_records=0, min_batch_records=2, max_batch_records=2),
        curator_persona="整理人格",
        prompt_template=template,
        config_revision=REVISION,
        temporal_policy=_policy(),
    )
    proposal = await service.propose(force=True)
    assert proposal is not None
    draft = gateway.calls[0]
    prompt = draft.request.messages[1].content
    assert prompt.count("[时间：") >= 1
    assert prompt.count("[时间范围：") >= 2
    assert "META\n[时间" not in prompt
    assert "SCHEMA\n[时间" not in prompt
    included = tuple(part for part in draft.parts if part.payload_pointer == "/messages/1/content")
    assert len(included) > 3
    for part in included:
        assert prompt[part.text_span[0] : part.text_span[1]] == part.content
    for left, right in zip(sorted(included, key=lambda item: item.text_span), sorted(included, key=lambda item: item.text_span)[1:], strict=False):
        assert left.text_span[1] <= right.text_span[0]
