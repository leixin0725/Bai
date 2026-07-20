"""[2026-07-19] 配置测试证明引用图、路径边界和外部凭据均 fail closed。"""

from pathlib import Path

import pytest

from bai_agent.config.loader import load_config
from bai_agent.config.validation import validate_debug_prompt, validate_provider_capabilities
from bai_agent.domain.errors import BaiError


def test_repository_config_loads_as_immutable_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder-invalid-for-tests")
    snapshot = load_config(Path("config"), require_credentials=True)
    assert snapshot.revision.startswith("sha256:")
    assert snapshot.default_state_id == "default"
    assert {p.persona_id for p in snapshot.personas} == {
        "chat",
        "memory_curator",
        "state_default",
    }
    assert all(p.prompt for p in snapshot.personas)


def test_missing_external_credential_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(BaiError, match="环境变量") as raised:
        load_config(Path("config"), require_credentials=True)
    assert raised.value.code == "CREDENTIAL_MISSING"


def test_path_escape_and_invalid_cross_field_are_rejected(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent.toml").write_text(
        'schema_version=1\nagent_id="bai"\ndata_root="../data"\n'
        '[personas]\nchat="../outside.md"\nmemory_curator="../outside.md"\n'
        '[prompts]\nchat_context="../outside.md"\nmemory_curation="../outside.md"\n'
        'untrusted_memory_boundary="../outside.md"\n'
        '[archive]\nsegment_max_records=1\nsegment_max_bytes=100\nmax_record_bytes=101\n'
        '[short_term]\nmax_records=4\nreserved_records=4\n'
        'curation_batch_min_records=2\ncuration_batch_max_records=1\n',
        encoding="utf-8",
    )
    with pytest.raises(BaiError) as raised:
        load_config(config_dir, require_credentials=False)
    assert raised.value.code in {"CONFIG_PATH_ESCAPE", "CONFIG_INVALID"}


def test_revision_changes_when_prompt_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from shutil import copytree

    copytree("config", tmp_path / "config")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder-invalid-for-tests")
    first = load_config(tmp_path / "config")
    prompt = tmp_path / "config" / "personas" / "chat.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\n测试标记。\n", encoding="utf-8")
    second = load_config(tmp_path / "config")
    assert first.revision != second.revision


@pytest.mark.parametrize("color", ["auto", "always", "never"])
def test_debug_prompt_policy_accepts_documented_values(color: str) -> None:
    policy = validate_debug_prompt({"color": color})
    assert policy["high_context_percent"] == 80
    assert policy["critical_context_percent"] == 95


@pytest.mark.parametrize(
    "value",
    [
        {"color": "sometimes"},
        {"high_context_percent": 95, "critical_context_percent": 80},
        {"estimate_safety_margin_percent": 51},
    ],
)
def test_debug_prompt_policy_rejects_invalid_values(value: dict) -> None:
    with pytest.raises(BaiError, match="debug_prompt"):
        validate_debug_prompt(value)


def test_provider_capability_validation_rejects_caps_and_unknown_estimator() -> None:
    provider = {"max_output_cap": 384000, "token_estimator": "deepseek_character_v1"}
    profile = {"context_window_tokens": 1000000, "max_output_tokens": 8192}
    validate_provider_capabilities(provider, profile)
    with pytest.raises(BaiError):
        validate_provider_capabilities(provider, profile | {"max_output_tokens": 400000})
    with pytest.raises(BaiError):
        validate_provider_capabilities(provider | {"token_estimator": "missing"}, profile)
