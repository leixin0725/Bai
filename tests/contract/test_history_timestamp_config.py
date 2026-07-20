"""[2026-07-20] 独立时间配置合同覆盖默认值、严格字段和快照资产。"""

from __future__ import annotations

from pathlib import Path
from shutil import copytree

import pytest

from bai_agent.config.loader import load_config
from bai_agent.config.validation import validate_history_timestamps
from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import content_hash


CANONICAL = {
    "schema_version": 1,
    "display_timezone": "Asia/Shanghai",
    "long_gap_minutes": 30,
    "continuous_segment_refresh_minutes": 120,
    "split_on_local_date_change": True,
}


def test_canonical_history_timestamp_config_is_loaded_as_revisioned_asset() -> None:
    snapshot = load_config(Path("config"), require_credentials=False)
    assert snapshot.settings["history_timestamps.toml"] == CANONICAL
    asset = next(item for item in snapshot.assets if item.asset_id == "config:history_timestamps")
    assert asset.kind == "history_timestamp_policy"
    assert asset.project_relative_path == "history_timestamps.toml"
    assert asset.content_sha256 == content_hash(asset.content)
    assert asset.revision == snapshot.revision


@pytest.mark.parametrize(
    "invalid",
    [
        CANONICAL | {"unknown": 1},
        {key: value for key, value in CANONICAL.items() if key != "display_timezone"},
        CANONICAL | {"schema_version": True},
        CANONICAL | {"schema_version": 2},
        CANONICAL | {"display_timezone": "Local"},
        CANONICAL | {"long_gap_minutes": True},
        CANONICAL | {"long_gap_minutes": 0},
        CANONICAL | {"long_gap_minutes": 1441},
        CANONICAL | {"continuous_segment_refresh_minutes": 0},
        CANONICAL | {"continuous_segment_refresh_minutes": 10081},
        CANONICAL | {"continuous_segment_refresh_minutes": 29},
        CANONICAL | {"split_on_local_date_change": 1},
    ],
)
def test_history_timestamp_config_rejects_unknown_missing_type_range_relation_and_zone(invalid: dict[str, object]) -> None:
    with pytest.raises(BaiError) as raised:
        validate_history_timestamps(invalid)
    assert raised.value.code == "CONFIG_INVALID"
    assert "history_timestamps.toml" in raised.value.safe_message


def test_history_timestamp_manifest_is_required_and_changes_snapshot_revision(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    copytree("config", config_dir)
    first = load_config(config_dir, require_credentials=False)
    timestamp_config = config_dir / "history_timestamps.toml"
    timestamp_config.write_text(timestamp_config.read_text(encoding="utf-8").replace("long_gap_minutes = 30", "long_gap_minutes = 60"), encoding="utf-8")
    second = load_config(config_dir, require_credentials=False)
    assert first.revision != second.revision
    assert second.settings["history_timestamps.toml"]["long_gap_minutes"] == 60
    timestamp_config.unlink()
    with pytest.raises(BaiError, match="history_timestamps.toml"):
        load_config(config_dir, require_credentials=False)
