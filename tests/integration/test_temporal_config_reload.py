"""[2026-07-20] 时间策略仅在完整轮次边界按整组运行时对象原子替换。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from shutil import copytree

import pytest

from bai_agent.application import build_application
from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import SourceKind, SourceRef, TemporalLogEntry, TemporalSpan, TemporalTimeKind
from bai_agent.prompting.temporal import annotate_history
from tests.fakes import FakeProvider


def _entries() -> tuple[TemporalLogEntry, ...]:
    source = SourceRef(
        source_kind=SourceKind.RUNTIME, source_id="reload-test", entity_ids=("reload-test",), producer="test"
    )
    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    return tuple(
        TemporalLogEntry(
            entry_id=f"entry-{index}", body=f"body-{index}",
            span=TemporalSpan(start=instant, end=instant, kind=TemporalTimeKind.EVENT),
            sources=(source,),
        )
        for index, instant in enumerate((start, start + timedelta(minutes=45)), start=1)
    )


def _replace(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def test_valid_reload_replaces_complete_runtime_and_freezes_old_snapshot(tmp_path: Path) -> None:
    config = tmp_path / "config"
    copytree("config", config)
    app = build_application(config, tmp_path / "data", provider=FakeProvider())
    try:
        timestamp_file = config / "history_timestamps.toml"
        old_controller = app.controller
        old_policy = old_controller.temporal_policy
        assert len(annotate_history(_entries(), old_policy).markers) == 2

        _replace(timestamp_file, "long_gap_minutes = 30", "long_gap_minutes = 60")
        assert len(annotate_history(_entries(), old_policy).markers) == 2
        app._reload_config()

        assert app.controller is not old_controller
        assert app.controller.temporal_policy is app.controller.prompt_assembler.temporal_policy
        assert app.controller.temporal_policy is app.controller.curation_service.temporal_policy
        assert len(annotate_history(_entries(), app.controller.temporal_policy).markers) == 1
        assert app.controller.temporal_policy.config_source.revision == app.snapshot.revision

        previous = app.controller
        _replace(timestamp_file, 'display_timezone = "Asia/Shanghai"', 'display_timezone = "UTC"')
        app._reload_config()
        assert app.controller is not previous
        assert app.controller.temporal_policy.display_timezone_name == "UTC"
        assert annotate_history(_entries()[:1], app.controller.temporal_policy).text.startswith(
            "[时间：2026-07-20 00:00 +00:00]"
        )
    finally:
        app.close()


@pytest.mark.asyncio
async def test_invalid_reload_has_no_partial_runtime_raw_tool_or_provider_effect_and_recovers(tmp_path: Path) -> None:
    config = tmp_path / "config"
    copytree("config", config)
    provider = FakeProvider(response="恢复成功")
    app = build_application(config, tmp_path / "data", provider=provider)
    try:
        timestamp_file = config / "history_timestamps.toml"
        old_controller = app.controller
        old_revision = app.snapshot.revision
        before_raw = tuple(app.archive.read_all())
        _replace(timestamp_file, "continuous_segment_refresh_minutes = 120", "continuous_segment_refresh_minutes = 29")
        with pytest.raises(BaiError, match="continuous_segment_refresh_minutes"):
            await app.run_turn("不得产生副作用")
        assert app.controller is old_controller
        assert app.snapshot.revision == old_revision
        assert app.archive.read_all() == before_raw
        assert provider.requests == []

        _replace(timestamp_file, "continuous_segment_refresh_minutes = 29", "continuous_segment_refresh_minutes = 120")
        assert await app.run_turn("修复后恢复") == "恢复成功"
        assert app.controller is not old_controller
        assert len(provider.requests) == 1
    finally:
        app.close()
