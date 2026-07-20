"""[2026-07-19] 每次加载生成单一不可变快照，轮次中不观察文件变化。"""

from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
import os
from pathlib import Path
import tomllib
from typing import Mapping

from bai_agent.config.validation import (
    read_utf8_nonempty,
    resolve_inside,
    validate_agent,
    validate_debug_prompt,
    validate_history_timestamps,
    validate_provider_capabilities,
    validate_template,
)
from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import ConfigAsset, ConfigSnapshot, PersonaProfile, content_hash


MANIFESTS = (
    "agent.toml",
    "providers.toml",
    "states.toml",
    "tools.toml",
    "logging.toml",
    "history_timestamps.toml",
)


def default_config_dir() -> Path:
    """[2026-07-20] 返回 wheel 内完整默认配置；editable 开发树回退到版本控制目录。"""
    packaged = files("bai_agent").joinpath("default_config")
    if packaged.is_dir():
        return Path(str(packaged))
    development = Path(__file__).resolve().parents[3] / "config"
    if development.is_dir():
        return development
    raise BaiError("CONFIG_DEFAULT_NOT_FOUND", "安装制品中的默认配置目录不可达。")


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BaiError(
            "CONFIG_INVALID",
            f"配置清单 {path.as_posix()} 缺失或 TOML 无效（字段 <toml>）。",
        ) from exc
    if value.get("schema_version") != 1:
        raise BaiError("CONFIG_SCHEMA_UNSUPPORTED", f"配置清单 {path.name} 的版本不受支持。")
    return value


def _data_root(config_root: Path, reference: str) -> Path:
    candidate = (config_root / reference).resolve()
    if candidate in {config_root.resolve(), config_root.resolve().parent} or candidate == Path(candidate.anchor):
        raise BaiError("CONFIG_INVALID", "data_root 不能是配置根、仓库根或文件系统根。")
    if str(candidate).startswith("\\\\"):
        raise BaiError("CONFIG_INVALID", "首版不接受网络共享数据根。")
    return candidate


def load_config(
    config_dir: Path,
    *,
    require_credentials: bool = True,
    environ: Mapping[str, str] | None = None,
) -> ConfigSnapshot:
    root = config_dir.resolve()
    documents = {name: _read_toml(root / name) for name in MANIFESTS}
    agent = documents["agent.toml"]
    validate_agent(agent)
    agent["debug_prompt"] = validate_debug_prompt(agent.get("debug_prompt"))
    documents["history_timestamps.toml"] = validate_history_timestamps(
        documents["history_timestamps.toml"]
    )
    env = os.environ if environ is None else environ

    providers = documents["providers.toml"].get("providers", [])
    if not isinstance(providers, list) or not providers:
        raise BaiError("CONFIG_INVALID", "至少需要一个 Provider 配置。")
    for provider in providers:
        variable = provider.get("api_key_env")
        if not isinstance(variable, str) or not variable:
            raise BaiError("CONFIG_INVALID", "Provider 必须声明凭据环境变量名。")
        if require_credentials and not env.get(variable):
            raise BaiError("CREDENTIAL_MISSING", f"所需凭据环境变量 {variable} 不存在。")

    loaded_paths: dict[str, Path] = {name: root / name for name in MANIFESTS}
    asset_ids: dict[str, tuple[str, str]] = {
        name: (
            f"config:{Path(name).stem}",
            "history_timestamp_policy"
            if name == "history_timestamps.toml"
            else "agent_config"
            if name == "agent.toml"
            else "provider_config"
            if name == "providers.toml"
            else "tool_config"
            if name == "tools.toml"
            else "state_prompt",
        )
        for name in MANIFESTS
    }
    persona_values: list[PersonaProfile] = []
    persona_paths: set[Path] = set()
    for persona_id, role in (("chat", "chat"), ("memory_curator", "memory_curator")):
        reference = agent.get("personas", {}).get(persona_id)
        if not isinstance(reference, str):
            raise BaiError("CONFIG_INVALID", "人格入口引用缺失。")
        path = resolve_inside(root, reference)
        if path in persona_paths:
            raise BaiError("CONFIG_INVALID", "不同人格职责不能引用同一提示文件。")
        persona_paths.add(path)
        loaded_paths[reference] = path
        asset_ids[reference] = (f"persona:{persona_id}", "persona")
        prompt = read_utf8_nonempty(path)
        persona_values.append(
            PersonaProfile(
                persona_id,
                role,
                reference,
                prompt,
                persona_id,
                prompt_sha256=content_hash(prompt),
            )
        )

    states = documents["states.toml"]
    state_personas = states.get("personas", [])
    state_ids: set[str] = set()
    for item in state_personas:
        persona_id = item.get("id")
        if not isinstance(persona_id, str) or persona_id in state_ids:
            raise BaiError("CONFIG_INVALID", "状态人格 ID 缺失或重复。")
        state_ids.add(persona_id)
        reference = item.get("prompt")
        if not isinstance(reference, str):
            raise BaiError("CONFIG_INVALID", "状态人格提示引用缺失。")
        path = resolve_inside(root, reference)
        if path in persona_paths:
            raise BaiError("CONFIG_INVALID", "不同人格职责不能引用同一提示文件。")
        persona_paths.add(path)
        loaded_paths[reference] = path
        asset_ids[reference] = (f"persona:{persona_id}", "state_prompt")
        prompt = read_utf8_nonempty(path)
        persona_values.append(
            PersonaProfile(
                persona_id,
                "state",
                reference,
                prompt,
                str(item.get("model_profile", "chat")),
                prompt_sha256=content_hash(prompt),
            )
        )
    known_personas = {item.persona_id for item in persona_values}
    for state in states.get("states", []):
        refs = state.get("ordered_persona_ids", [])
        if len(refs) != len(set(refs)) or any(ref not in known_personas for ref in refs):
            raise BaiError("CONFIG_INVALID", "状态人格引用缺失或重复。")

    model_profiles = documents["providers.toml"].get("model_profiles", {})
    if not isinstance(model_profiles, dict):
        raise BaiError("CONFIG_INVALID", "模型 profile 清单无效。")
    provider_ids = {item.get("id") for item in providers}
    for profile_id, profile in model_profiles.items():
        if not isinstance(profile, dict) or profile.get("provider") not in provider_ids:
            raise BaiError("CONFIG_INVALID", f"模型 profile {profile_id} 的 Provider 引用无效。")
        provider_capability = next(item for item in providers if item.get("id") == profile.get("provider"))
        validate_provider_capabilities(provider_capability, profile)
        if profile_id in {"chat", "memory_curator"} and (
            profile.get("model") != "deepseek-v4-flash"
            or profile.get("thinking_enabled") is not False
            or profile.get("max_output_tokens") != 8192
        ):
            raise BaiError("PROVIDER_CAPABILITY_INVALID", f"模型 profile {profile_id} 未满足 V4 Flash 迁移不变量。")
    if any(item.model_profile_id not in model_profiles for item in persona_values):
        raise BaiError("CONFIG_INVALID", "人格引用的模型 profile 不存在。")

    prompts: dict[str, str] = {}
    template_definitions = agent.get("template_variables", {})
    for prompt_id, reference in agent.get("prompts", {}).items():
        if not isinstance(reference, str):
            raise BaiError("CONFIG_INVALID", "提示模板引用必须是相对路径。")
        path = resolve_inside(root, reference)
        loaded_paths[reference] = path
        asset_ids[reference] = (f"prompt:{prompt_id}", "prompt_template")
        prompt_text = read_utf8_nonempty(path)
        definition = template_definitions.get(prompt_id)
        if not isinstance(definition, dict):
            raise BaiError("CONFIG_INVALID", "每个提示模板必须声明变量清单。")
        allowed = tuple(definition.get("allowed", ()))
        untrusted = tuple(definition.get("untrusted", ()))
        if not all(isinstance(item, str) for item in (*allowed, *untrusted)):
            raise BaiError("CONFIG_INVALID", "提示模板变量清单类型无效。")
        validate_template(
            prompt_text,
            allowed_variables=allowed,
            untrusted_variables=untrusted,
        )
        prompts[prompt_id] = prompt_text

    digest = sha256()
    for logical_name, path in sorted(loaded_paths.items()):
        digest.update(logical_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")

    revision = "sha256:" + digest.hexdigest()
    assets = tuple(
        ConfigAsset(
            asset_id=asset_ids[logical_name][0],
            kind=asset_ids[logical_name][1],
            project_relative_path=logical_name.replace("\\", "/"),
            content=path.read_text(encoding="utf-8-sig"),
            content_sha256=content_hash(path.read_text(encoding="utf-8-sig")),
            revision=revision,
        )
        for logical_name, path in sorted(loaded_paths.items())
    )

    return ConfigSnapshot.create(
        revision=revision,
        config_root=str(root),
        data_root=str(_data_root(root, str(agent["data_root"]))),
        default_state_id=str(states.get("default_state_id", "")),
        personas=tuple(persona_values),
        prompts=prompts,
        settings=documents,
        assets=assets,
    )
