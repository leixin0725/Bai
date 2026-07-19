"""[2026-07-19] 覆盖概览先于长期明细和近期原文，来源原文不自动注入。"""

from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import CoverageSpan, MemoryCoverageOverview, Role, StateResolutionResult
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.prompting.assembler import PromptAssembler


REVISION = "sha256:" + "1" * 64


def test_prompt_has_coverage_budget_trust_and_no_automatic_source_text(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    record = archive.append(
        role=Role.USER,
        content="来源原文不应自动出现",
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default",
        config_revision=REVISION,
    )
    span = CoverageSpan(
        start_sequence=1,
        end_sequence=1,
        batch_id="batch-00000000-0000-4000-8000-000000000001",
        record_ids=(record.record_id,),
        record_hashes=(record.content_sha256,),
    )
    context = PromptAssembler.mvp("基础人格", ("状态人格",)).assemble(
        flow_id="flow",
        turn_id="turn",
        config_revision=REVISION,
        state_resolution=StateResolutionResult(
            state_id="default", ordered_persona_ids=("state_default",), resolver_id="static", resolver_version="1", reason_code="configured_default"
        ),
        memory_overview=MemoryCoverageOverview(revision=1, text="已覆盖一条事实", coverage_spans=(span,)),
        long_term_memories=("相关长期明细",),
        recent_records=(),
        current_input="当前问题",
        all_raw_records=(record,),
        curated_through=1,
        budgets={"overview_chars": 100, "long_term_chars": 100, "recent_chars": 100},
    )
    ids = [item.segment_id for item in context.segments]
    assert ids.index("memory_overview") < ids.index("long_term_memories") < ids.index("recent_records")
    rendered = "\n".join(item.content for item in context.segments)
    assert "来源原文不应自动出现" not in rendered
    assert context.coverage["covered_range"] == [1, 1]
    assert any(
        item.get("source_id") == record.record_id and item.get("sha256") == record.content_sha256
        for item in context.source_manifest
    )


def test_coverage_gap_stops_context_construction(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    record = archive.append(
        role=Role.USER, content="记录", turn_id="turn-00000000-0000-4000-8000-000000000001", state_id="default", config_revision=REVISION
    )
    with pytest.raises(BaiError):
        PromptAssembler.mvp("基础人格", ("状态人格",)).assemble(
            flow_id="flow", turn_id="turn", config_revision=REVISION,
            state_resolution=StateResolutionResult(state_id="default", ordered_persona_ids=("state_default",), resolver_id="static", resolver_version="1", reason_code="configured_default"),
            memory_overview=MemoryCoverageOverview.empty(), long_term_memories=(), recent_records=(), current_input="问题",
            all_raw_records=(record,), curated_through=0,
        )
