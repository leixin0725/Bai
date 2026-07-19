"""[2026-07-19] 应用装配只连接配置快照、领域端口和适配器，不复制业务规则。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bai_agent.config.loader import load_config
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.recovery import WriterLease
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.providers.registry import create_provider
from bai_agent.runtime.controller import SingleTurnController
from bai_agent.states.resolver import StaticStateResolver


class AgentApplication:
    def __init__(self, snapshot, archive, controller, lease: WriterLease) -> None:
        self.snapshot = snapshot
        self.archive = archive
        self.controller = controller
        self.lease = lease

    async def run_turn(self, content: str, *, resume_pending: bool = False, turn_id: str | None = None) -> str:
        return await self.controller.run_turn(
            content,
            resume_pending=resume_pending,
            turn_id=turn_id,
            config_revision=self.snapshot.revision,
        )

    def close(self) -> None:
        self.lease.release()


def build_application(
    config_dir: Path,
    data_dir: Path | None = None,
    *,
    provider: Any | None = None,
    on_output=None,
    tracer=None,
) -> AgentApplication:
    snapshot = load_config(config_dir, require_credentials=provider is None)
    settings = snapshot.settings
    memory_root = (data_dir or Path(snapshot.data_root)) / "memory"
    lease = WriterLease(memory_root / ".state" / "writer.lock", float(settings["agent.toml"]["runtime"]["writer_lock_timeout_seconds"]))
    lease.acquire()
    try:
        archive_settings = settings["agent.toml"]["archive"]
        archive = RawRecordArchive(memory_root, **archive_settings)
        archive.validate_permissions()
        states_doc = settings["states.toml"]
        states = {str(item["id"]): tuple(item["ordered_persona_ids"]) for item in states_doc["states"] if item.get("enabled", False)}
        resolver = StaticStateResolver(snapshot.default_state_id, states)
        personas = {item.persona_id: item.prompt for item in snapshot.personas}
        state_prompts = tuple(personas[item] for item in states[snapshot.default_state_id])
        assembler = PromptAssembler.mvp(personas["chat"], state_prompts)
        if provider is None:
            providers = settings["providers.toml"]
            profile = providers["model_profiles"]["chat"]
            provider_config = next(item for item in providers["providers"] if item["id"] == profile["provider"])
            provider = create_provider(provider_config, profile)
        controller = SingleTurnController(
            archive, provider, resolver, assembler, on_output=on_output, tracer=tracer
        )
        return AgentApplication(snapshot, archive, controller, lease)
    except Exception:
        lease.release()
        raise
