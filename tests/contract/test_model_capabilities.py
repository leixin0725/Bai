"""[2026-07-20] 模型能力合同固定 V4 Flash 迁移与两个 profile 的生成参数不变量。"""

from pathlib import Path
import tomllib

from bai_agent.config.validation import validate_provider_capabilities


def test_deepseek_v4_flash_capabilities_and_profiles() -> None:
    with Path("config/providers.toml").open("rb") as handle:
        config = tomllib.load(handle)
    provider = config["providers"][0]
    assert provider["max_output_cap"] == 384000
    assert provider["token_estimator"] == "deepseek_character_v1"
    chat = config["model_profiles"]["chat"]
    curator = config["model_profiles"]["memory_curator"]
    for profile in (chat, curator):
        assert profile["model"] == "deepseek-v4-flash"
        assert profile["context_window_tokens"] == 1000000
        assert profile["thinking_enabled"] is False
        assert profile["max_output_tokens"] == 8192
        validate_provider_capabilities(provider, profile)
    assert chat["temperature"] == 0.7 and chat["tools_enabled"] is True and chat["structured_output"] is False
    assert curator["tools_enabled"] is True and curator["structured_output"] is True
    assert curator["output_schema"] == "memory_curation_v1"
