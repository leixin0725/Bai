"""[2026-07-20] 配置资产测试证明来源身份来自加载快照而非正文猜测。"""

from pathlib import Path
from shutil import copytree

import pytest

from bai_agent.config.loader import load_config
from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import ConfigAsset, content_hash


def test_config_asset_is_immutable_and_snapshot_preserves_old_content(tmp_path: Path) -> None:
    copytree("config", tmp_path / "config")
    snapshot = load_config(tmp_path / "config", require_credentials=False)
    asset = next(item for item in snapshot.assets if item.asset_id == "persona:chat")
    assert asset.project_relative_path == "personas/chat.md"
    assert asset.content_sha256 == content_hash(asset.content)
    assert asset.revision == snapshot.revision
    (tmp_path / "config" / "personas" / "chat.md").write_text("后来内容", encoding="utf-8")
    assert asset.content != "后来内容"


def test_config_asset_rejects_path_escape() -> None:
    with pytest.raises((BaiError, ValueError)):
        ConfigAsset(
            asset_id="bad",
            kind="persona",
            project_relative_path="../outside.md",
            content="x",
            content_sha256=content_hash("x"),
            revision="sha256:" + "0" * 64,
        )
