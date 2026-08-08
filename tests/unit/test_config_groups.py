"""[2026-08-08] 配置分组清单与错误定位：分组仅用于校验，生效仍为整份快照原子切换。"""

from __future__ import annotations

from bai_agent.config.loader import CONFIG_GROUP_BY_FILE, CONFIG_GROUPS, describe_config_error
from bai_agent.domain.errors import BaiError


def test_config_groups_cover_all_manifests_and_prompt_files() -> None:
    assert CONFIG_GROUPS == (
        "agent",
        "providers",
        "states",
        "tools",
        "logging",
        "history_timestamps",
        "personas",
        "prompts",
    )
    assert set(CONFIG_GROUP_BY_FILE) == {
        "agent.toml",
        "providers.toml",
        "states.toml",
        "tools.toml",
        "logging.toml",
        "history_timestamps.toml",
    }


def test_describe_config_error_locates_group_field_and_reason() -> None:
    exc = BaiError("CONFIG_INVALID", "history_timestamps.toml 字段 long_gap_minutes 必须是 1..1440 的整数。")
    detail = describe_config_error(exc)
    assert detail["group"] == "history_timestamps"
    assert detail["field"] == "long_gap_minutes"
    assert "必须是 1..1440" in detail["reason"]


def test_describe_config_error_falls_back_to_personas_and_config() -> None:
    persona = describe_config_error(BaiError("CONFIG_INVALID", "人格入口引用缺失。"))
    assert persona["group"] == "personas"
    fallback = describe_config_error(BaiError("CONFIG_INVALID", "Provider 必须声明凭据环境变量名。"))
    assert fallback["group"] == "config"
