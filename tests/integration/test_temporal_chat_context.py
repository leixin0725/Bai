"""[2026-07-20] 控制器到模型请求的近期历史使用持久化事件时间和统一段边界。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bai_agent.domain.models import Role, SourceKind, SourceRef, TemporalSegmentationPolicy
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.runtime.controller import SingleTurnController
from bai_agent.states.resolver import StaticStateResolver
from tests.fakes import FakeProvider


REVISION = "sha256:" + "1" * 64
BASE = datetime(2026, 7, 20, tzinfo=timezone.utc)


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


def _populate(archive: RawRecordArchive, offsets: list[int]) -> None:
    for index, minute in enumerate(offsets, start=1):
        turn = (index + 1) // 2
        archive.append(
            role=Role.USER if index % 2 else Role.ASSISTANT,
            content=f"历史-{index}",
            turn_id=f"turn-00000000-0000-4000-8000-{turn:012d}",
            state_id="default",
            config_revision=REVISION,
            record_id=f"rec-00000000-0000-4000-8000-{index:012d}",
            created_at=BASE + timedelta(minutes=minute),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("offsets", "expected_markers"),
    [
        ([0, 5], 1),
        ([0, 30], 2),
        ([0, 25, 50, 75, 100, 120], 2),
        ([15 * 60 + 59, 16 * 60 + 1], 2),
        ([0, 0], 1),
        ([10, 0], 2),
    ],
)
async def test_recent_history_boundaries_reach_debug_off_provider_request(
    tmp_path: Path,
    offsets: list[int],
    expected_markers: int,
) -> None:
    archive = RawRecordArchive(tmp_path / "memory")
    _populate(archive, offsets)
    provider = FakeProvider()
    controller = SingleTurnController(
        archive,
        provider,
        StaticStateResolver.default(),
        PromptAssembler.mvp("基础人格", ("状态人格",), temporal_policy=_policy()),
        memory_budgets={"recent_chars": 10_000},
    )
    await controller.run_turn(
        "当前输入不应标注",
        turn_id="turn-00000000-0000-4000-8000-999999999999",
        config_revision=REVISION,
    )
    request = provider.requests[0]
    recent = request.messages[4]
    current = request.messages[5]
    assert sum(line.startswith("[时间：") for line in recent.content.splitlines()) == expected_markers
    assert "当前输入不应标注" not in recent.content
    assert current.content == "当前输入不应标注"
    assert all(f"历史-{index}" in recent.content for index in range(1, len(offsets) + 1))
