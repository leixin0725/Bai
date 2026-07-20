"""[2026-07-20] 工具时间标注不得改变 assistant/tool 协议字段。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bai_agent.domain.models import (
    SourceKind,
    SourceRef,
    TemporalSegmentationPolicy,
    ToolCall,
    ToolHistoryEvent,
    ToolHistoryEventKind,
)
from bai_agent.runtime.controller import render_tool_history


REVISION = "sha256:" + "1" * 64


def _source(identity: str) -> SourceRef:
    return SourceRef(source_kind=SourceKind.RUNTIME, source_id=identity, entity_ids=(identity,), producer="test")


def _policy() -> TemporalSegmentationPolicy:
    return TemporalSegmentationPolicy(
        display_timezone=ZoneInfo("Asia/Shanghai"), display_timezone_name="Asia/Shanghai",
        long_gap=timedelta(minutes=30), continuous_refresh=timedelta(minutes=120),
        split_on_local_date_change=True,
        config_source=SourceRef(
            source_kind=SourceKind.CONFIG_FILE, source_id="config:history_timestamps",
            project_relative_path="config/history_timestamps.toml",
            content_sha256="sha256:" + "2" * 64, revision=REVISION, producer="config_loader",
        ),
    )


def test_marker_only_assistant_and_results_keep_protocol_and_canonical_body() -> None:
    start = datetime(2026, 7, 20, 15, 50, tzinfo=timezone.utc)
    calls = (
        ToolCall(call_id="call-1", name="first", arguments={"x": 1}),
        ToolCall(call_id="call-2", name="second", arguments={"x": 2}),
    )
    events = (
        ToolHistoryEvent(
            event_id="tool-call-batch:origin-1", kind=ToolHistoryEventKind.TOOL_CALL_BATCH,
            occurred_at=start, original_body="", role="assistant", tool_calls=calls,
            sources=(_source("origin-1"),),
        ),
        ToolHistoryEvent(
            event_id="tool-result:call-1:one", kind=ToolHistoryEventKind.TOOL_RESULT,
            occurred_at=start + timedelta(minutes=5), original_body='{"data":{"n":1},"outcome":"success"}',
            role="tool", tool_call_id="call-1", sources=(_source("call-1"),),
        ),
        ToolHistoryEvent(
            event_id="tool-result:call-2:two", kind=ToolHistoryEventKind.TOOL_RESULT,
            occurred_at=start + timedelta(minutes=45), original_body='{"data":{"n":2},"outcome":"success"}',
            role="tool", tool_call_id="call-2", sources=(_source("call-2"),),
        ),
        ToolHistoryEvent(
            event_id="tool-call-batch:origin-2", kind=ToolHistoryEventKind.TOOL_CALL_BATCH,
            occurred_at=start + timedelta(minutes=10), original_body="继续", role="assistant",
            tool_calls=(ToolCall(call_id="call-3", name="third", arguments={"x": 3}),),
            sources=(_source("origin-2"),),
        ),
        ToolHistoryEvent(
            event_id="tool-result:call-3:three", kind=ToolHistoryEventKind.TOOL_RESULT,
            occurred_at=start + timedelta(minutes=11), original_body='{"data":{"n":3},"outcome":"success"}',
            role="tool", tool_call_id="call-3", sources=(_source("call-3"),),
        ),
    )
    messages, parts = render_tool_history(events, _policy(), message_offset=3, part_order=7)
    assert [message.role for message in messages] == ["assistant", "tool", "tool", "assistant", "tool"]
    assert messages[0].content.startswith("[时间：") and messages[0].content.endswith("]")
    assert tuple(call["call_id"] for call in messages[0].tool_calls) == ("call-1", "call-2")
    assert messages[1].tool_call_id == "call-1"
    assert messages[2].tool_call_id == "call-2"
    assert messages[1].content.endswith(events[1].original_body)
    assert messages[2].content.endswith(events[2].original_body)
    assert messages[3].content.startswith("[时间：") and messages[3].content.endswith("继续")
    assert messages[3].tool_calls[0]["call_id"] == "call-3"
    assert messages[4].tool_call_id == "call-3"
    assert all(message.role not in {"system", "user", "metadata"} for message in messages)
    first_marker = next(part for part in parts if part.content.startswith("[时间："))
    assert {source.source_id for source in first_marker.sources} >= {
        "config:history_timestamps", "origin-1",
    }
    for part in parts:
        index = int(part.payload_pointer.split("/")[2])
        assert messages[index - 3].content[part.text_span[0] : part.text_span[1]] == part.content
