"""[2026-07-19] 所有人格经同一只读工具获得同序、同错误和 flow 隔离结果。"""

from pathlib import Path
from hashlib import sha256

import pytest

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bai_agent.domain.models import (
    Role, SourceKind, SourceRef, TemporalSegmentationPolicy, ToolExecutionContext,
    ToolHistoryEvent, ToolHistoryEventKind, ToolOutcome, ToolResult, canonical_json,
)
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.long_term import LongTermStore
from bai_agent.tools.memory_source import MemorySourceQueryTool
from bai_agent.runtime.controller import render_tool_history


REVISION = "sha256:" + "1" * 64


def test_all_personas_receive_same_paginated_sources_without_writes(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    record = archive.append(
        role=Role.USER, content="来源", turn_id="turn-00000000-0000-4000-8000-000000000001", state_id="default", config_revision=REVISION
    )
    store = LongTermStore(tmp_path, archive)
    memory_id = store.initialize_with_manual_memory("事实", (record,)).memories[0].memory_id
    tool = MemorySourceQueryTool(store, archive, page_size=1)
    before = {path.name: sha256(path.read_bytes()).hexdigest() for path in [store.path, *archive.raw_dir.glob("*.jsonl")]}
    payloads = []
    for persona in ("chat", "memory_curator", "state_default", "helper_a", "helper_b"):
        context = ToolExecutionContext(flow_id=f"flow-{persona}", turn_id="turn", persona_id=persona, state_id="default", config_revision=REVISION)
        result = tool.execute_sync({"memory_id": memory_id}, context)
        assert result.outcome == "success"
        assert result.data["records"][0]["content"] == "来源"
        assert result.data["flow_id"] == f"flow-{persona}"
        payloads.append({key: value for key, value in result.data.items() if key != "flow_id"})
    assert payloads.count(payloads[0]) == len(payloads)
    after = {path.name: sha256(path.read_bytes()).hexdigest() for path in [store.path, *archive.raw_dir.glob("*.jsonl")]}
    assert before == after


def test_missing_memory_has_stable_error(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    store = LongTermStore(tmp_path, archive)
    store.initialize()
    tool = MemorySourceQueryTool(store, archive)
    context = ToolExecutionContext(flow_id="flow", turn_id="turn", persona_id="chat", state_id="default", config_revision=REVISION)
    result = tool.execute_sync({"memory_id": "mem-00000000-0000-4000-8000-000000000099"}, context)
    assert result.outcome == "not_found"


def test_direct_golden_result_is_unchanged_inside_outer_tool_history(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    record = archive.append(
        role=Role.USER, content="来源", turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default", config_revision=REVISION,
    )
    store = LongTermStore(tmp_path, archive)
    memory_id = store.initialize_with_manual_memory("事实", (record,)).memories[0].memory_id
    direct = MemorySourceQueryTool(store, archive).execute_sync(
        {"memory_id": memory_id},
        ToolExecutionContext(flow_id="flow", turn_id="turn", persona_id="chat", state_id="default", config_revision=REVISION),
    )
    body = canonical_json(direct.model_dump(mode="json"))
    event = ToolHistoryEvent(
        event_id="tool-result:call-1:golden", kind=ToolHistoryEventKind.TOOL_RESULT,
        occurred_at=datetime(2026, 7, 20, tzinfo=timezone.utc), original_body=body,
        role="tool", tool_call_id="call-1",
        sources=(SourceRef(
            source_kind=SourceKind.RUNTIME, source_id="tool-result:call-1",
            entity_ids=("call-1",), producer="tool_executor",
        ),),
    )
    policy = TemporalSegmentationPolicy(
        display_timezone=ZoneInfo("Asia/Shanghai"), display_timezone_name="Asia/Shanghai",
        long_gap=timedelta(minutes=30), continuous_refresh=timedelta(minutes=120),
        split_on_local_date_change=True,
        config_source=SourceRef(
            source_kind=SourceKind.CONFIG_FILE, source_id="config:history_timestamps",
            project_relative_path="config/history_timestamps.toml",
            content_sha256="sha256:" + "2" * 64, revision=REVISION, producer="config_loader",
        ),
    )
    messages, _ = render_tool_history((event,), policy, message_offset=0, part_order=0)
    assert messages[0].content.split("\n", 1)[1] == body
    assert direct == ToolResult.model_validate(direct.model_dump())
