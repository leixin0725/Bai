"""[2026-07-19] 验收清单把每项成功标准绑定到可执行测试，并补充高规模完整性证明。"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from shutil import copytree

from bai_agent.application import build_application
from bai_agent.domain.models import ToolExecutionContext, ToolOutcome
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.long_term import LongTermStore
from bai_agent.memory.selection import select_recent_complete_turns, validate_complete_coverage
from bai_agent.tools.memory_source import MemorySourceQueryTool
from tests.fixtures.performance import (
    CURATED_THROUGH,
    LONG_TERM_MEMORY_COUNT,
    RAW_RECORD_COUNT,
    RECENT_RECORD_COUNT,
    prepare_performance_dataset,
)
from tests.fakes import FakeProvider


ROOT = Path(__file__).resolve().parents[2]

# [2026-07-19] 清单记录跨文件验收证据；完整 pytest 会执行这些节点，而本测试防止重命名后留下静默缺口。
ACCEPTANCE_EVIDENCE: dict[str, tuple[str, ...]] = {
    "SC-001": ("tests/integration/test_restart_continuity.py::test_empty_and_one_hundred_turns_across_ten_restarts",),
    "SC-002": ("tests/contract/test_mvp_prompt_context.py::test_mvp_context_has_every_required_segment_in_order",),
    "SC-003": ("tests/integration/test_full_acceptance.py::test_high_scale_coverage_sources_and_read_only_query",),
    "SC-004": ("tests/fault_injection/test_raw_atomicity.py::test_half_line_tail_fails_closed_without_changing_bytes",),
    "SC-005": ("tests/integration/test_persona_reload.py::test_persona_marker_and_revision_switch_only_between_turns",),
    "SC-006": ("tests/integration/test_state_persona_composition.py::test_three_states_compose_in_order_and_persist_state",),
    "SC-007": (
        "tests/integration/test_tool_extension.py::test_default_registry_excludes_future_tool_until_explicit_enable",
        "tests/integration/test_autonomous_loop.py::test_loop_stops_at_configured_budget",
    ),
    "SC-008": ("tests/performance/test_startup.py::test_windows_reference_fresh_process_startup",),
    "SC-009": ("tests/integration/test_repository_secret_safety.py::test_repository_and_reachable_history_have_no_usable_credentials",),
    "SC-010": ("tests/integration/test_curation_workflow.py::test_threshold_zero_call_then_single_joint_commit",),
    "SC-011": ("tests/integration/test_long_term_store.py::test_valid_manual_text_edit_is_marked_and_revisioned",),
    "SC-012": ("tests/integration/test_curation_workflow.py::test_empty_extraction_advances_coverage_and_failure_does_not",),
    "SC-013": ("tests/integration/test_raw_file_permissions.py::test_permission_anomaly_fails_validation_commands",),
    "SC-014": ("tests/integration/test_full_acceptance.py::test_high_scale_coverage_sources_and_read_only_query",),
    "SC-015": ("tests/fault_injection/test_long_term_atomicity.py::test_pre_replace_failure_keeps_entire_old_revision",),
    "SC-016": ("tests/contract/test_memory_source_tool.py::test_all_personas_receive_same_paginated_sources_without_writes",),
    "SC-017": ("tests/contract/test_prompt_context.py::test_prompt_has_coverage_budget_trust_and_no_automatic_source_text",),
    "SC-018": ("tests/integration/test_packaging.py::test_compatibility_workflow_covers_supported_matrix",),
}


def test_acceptance_evidence_is_complete_and_resolvable() -> None:
    assert set(ACCEPTANCE_EVIDENCE) == {f"SC-{index:03d}" for index in range(1, 19)}
    for node_ids in ACCEPTANCE_EVIDENCE.values():
        assert node_ids
        for node_id in node_ids:
            relative_path, test_name = node_id.split("::", maxsplit=1)
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            assert f"def {test_name}(" in source


def test_high_scale_coverage_sources_and_read_only_query(tmp_path: Path) -> None:
    dataset = prepare_performance_dataset(tmp_path / "acceptance-data")
    archive = RawRecordArchive(dataset.memory_root)
    store = LongTermStore(dataset.memory_root, archive)
    records = archive.read_all()
    document = store.load()
    recent = select_recent_complete_turns(
        records,
        curated_through=document.curation.curated_through_sequence,
        max_records=RECENT_RECORD_COUNT,
    )
    coverage = validate_complete_coverage(
        records,
        document.coverage_overview,
        curated_through=document.curation.curated_through_sequence,
        recent_records=recent,
    )

    assert len(records) == RAW_RECORD_COUNT
    assert len(document.memories) == LONG_TERM_MEMORY_COUNT
    assert coverage.covered_range == (1, CURATED_THROUGH)
    assert coverage.direct_range == (CURATED_THROUGH + 1, RAW_RECORD_COUNT)
    represented = {
        sequence
        for span in document.coverage_overview.coverage_spans
        for sequence in range(span.start_sequence, span.end_sequence + 1)
    } | {record.global_sequence for record in recent}
    assert represented == set(range(1, RAW_RECORD_COUNT + 1))

    raw_by_id = {record.record_id: record for record in records}
    assert all(
        reference.record_id in raw_by_id
        and raw_by_id[reference.record_id].content_sha256 == reference.record_sha256
        for memory in document.memories
        for reference in memory.source_refs
    )
    assert all(
        raw_by_id[reference.record_id].global_sequence <= CURATED_THROUGH
        for memory in document.memories
        for reference in memory.source_refs
    )

    before = {
        path.relative_to(dataset.memory_root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in dataset.memory_root.rglob("*")
        if path.is_file()
    }
    result = MemorySourceQueryTool(store, archive).execute_sync(
        {"memory_id": document.memories[-1].memory_id},
        ToolExecutionContext(
            flow_id="full-acceptance",
            turn_id="full-acceptance",
            persona_id="chat",
            state_id="default",
            config_revision="sha256:" + "7" * 64,
        ),
    )
    after = {
        path.relative_to(dataset.memory_root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in dataset.memory_root.rglob("*")
        if path.is_file()
    }
    assert result.outcome == ToolOutcome.SUCCESS
    assert result.data["source_count"] >= 1
    assert result.data["records"][0]["global_sequence"] <= CURATED_THROUGH
    assert after == before


def test_all_temporal_consumers_share_one_revision_and_reload_without_storage_rewrite(tmp_path: Path) -> None:
    config = tmp_path / "config"
    copytree("config", config)
    app = build_application(config, tmp_path / "data", provider=FakeProvider())
    try:
        archive_before = {
            path.relative_to(app.archive.memory_root).as_posix(): path.read_bytes()
            for path in app.archive.memory_root.rglob("*")
            if path.is_file() and path.name != "writer.lock"
        }
        policy = app.controller.temporal_policy
        assert policy is app.controller.prompt_assembler.temporal_policy
        assert policy is app.controller.curation_service.temporal_policy
        assert policy.config_source.revision == app.snapshot.revision
        timestamp_file = config / "history_timestamps.toml"
        timestamp_file.write_text(
            timestamp_file.read_text(encoding="utf-8").replace("long_gap_minutes = 30", "long_gap_minutes = 60"),
            encoding="utf-8",
        )
        app._reload_config()
        reloaded = app.controller.temporal_policy
        assert reloaded is app.controller.prompt_assembler.temporal_policy
        assert reloaded is app.controller.curation_service.temporal_policy
        assert reloaded.config_source.revision == app.snapshot.revision
        assert reloaded.long_gap.total_seconds() == 3600
        archive_after = {
            path.relative_to(app.archive.memory_root).as_posix(): path.read_bytes()
            for path in app.archive.memory_root.rglob("*")
            if path.is_file() and path.name != "writer.lock"
        }
        assert archive_after == archive_before
    finally:
        app.close()
