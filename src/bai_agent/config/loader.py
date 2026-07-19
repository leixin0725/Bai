"""[2026-07-19] 每次加载生成单一不可变快照，轮次中不观察文件变化。"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import tomllib
from typing import Mapping

from bai_agent.config.validation import read_utf8_nonempty, resolve_inside, validate_agent
from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import ConfigSnapshot, PersonaProfile


MANIFESTS = ("agent.toml", "providers.toml", "states.toml", "tools.toml", "logging.toml")


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BaiError("CONFIG_INVALID", f"配置清单 {path.name} 缺失或无效。") from exc
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
    persona_values: list[PersonaProfile] = []
    for persona_id, role in (("chat", "chat"), ("memory_curator", "memory_curator")):
        reference = agent.get("personas", {}).get(persona_id)
        if not isinstance(reference, str):
            raise BaiError("CONFIG_INVALID", "人格入口引用缺失。")
        path = resolve_inside(root, reference)
        loaded_paths[reference] = path
        persona_values.append(
            PersonaProfile(persona_id, role, reference, read_utf8_nonempty(path), persona_id)
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
        loaded_paths[reference] = path
        persona_values.append(
            PersonaProfile(
                persona_id,
                "state",
                reference,
                read_utf8_nonempty(path),
                str(item.get("model_profile", "chat")),
            )
        )
    known_personas = {item.persona_id for item in persona_values}
    for state in states.get("states", []):
        refs = state.get("ordered_persona_ids", [])
        if len(refs) != len(set(refs)) or any(ref not in known_personas for ref in refs):
            raise BaiError("CONFIG_INVALID", "状态人格引用缺失或重复。")

    prompts: dict[str, str] = {}
    for prompt_id, reference in agent.get("prompts", {}).items():
        if not isinstance(reference, str):
            raise BaiError("CONFIG_INVALID", "提示模板引用必须是相对路径。")
        path = resolve_inside(root, reference)
        loaded_paths[reference] = path
        prompts[prompt_id] = read_utf8_nonempty(path)

    digest = sha256()
    for logical_name, path in sorted(loaded_paths.items()):
        digest.update(logical_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")

    return ConfigSnapshot.create(
        revision="sha256:" + digest.hexdigest(),
        config_root=str(root),
        data_root=str(_data_root(root, str(agent["data_root"]))),
        default_state_id=str(states.get("default_state_id", "")),
        personas=tuple(persona_values),
        prompts=prompts,
        settings=documents,
    )

