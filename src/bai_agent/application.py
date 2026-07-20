"""[2026-07-19] 应用装配只连接配置快照、领域端口和适配器，不复制业务规则。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bai_agent.config.loader import load_config
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.curation import CurationPolicy, CurationService
from bai_agent.memory.long_term import LongTermStore
from bai_agent.memory.recovery import WriterLease
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.prompting.personas import PersonaPromptSet
from bai_agent.providers.registry import create_provider
from bai_agent.runtime.controller import SingleTurnController
from bai_agent.states.resolver import StaticStateResolver
from bai_agent.tools.executor import ToolExecutor
from bai_agent.tools.memory_source import MemorySourceQueryTool
from bai_agent.tools.registry import ToolRegistry


class AgentApplication:
    def __init__(
        self,
        snapshot,
        archive,
        long_term_store,
        controller,
        lease: WriterLease,
        *,
        config_dir: Path,
        managed_provider: bool,
    ) -> None:
        self.snapshot = snapshot
        self.archive = archive
        self.long_term_store = long_term_store
        self.controller = controller
        self.lease = lease
        self.config_dir = config_dir
        self.managed_provider = managed_provider

    def _reload_config(self) -> None:
        fresh = load_config(self.config_dir, require_credentials=self.managed_provider)
        if fresh.revision == self.snapshot.revision:
            return
        settings = fresh.settings
        states_doc = settings["states.toml"]
        states = {
            str(item["id"]): tuple(item["ordered_persona_ids"])
            for item in states_doc["states"]
            if item.get("enabled", False)
        }
        resolver = StaticStateResolver(fresh.default_state_id, states)
        persona_prompts = PersonaPromptSet.from_snapshot(fresh)
        assembler = PromptAssembler.mvp(
            persona_prompts.trusted_chat_instruction,
            persona_prompts.state_prompts(states[fresh.default_state_id]),
        )
        chat_provider = self.controller.provider
        curator_provider = self.controller.curation_service.provider
        if self.managed_provider:
            providers = settings["providers.toml"]
            chat_profile = providers["model_profiles"]["chat"]
            chat_config = next(
                item for item in providers["providers"] if item["id"] == chat_profile["provider"]
            )
            chat_provider = create_provider(chat_config, chat_profile)
            curator_profile = providers["model_profiles"]["memory_curator"]
            curator_config = next(
                item for item in providers["providers"] if item["id"] == curator_profile["provider"]
            )
            curator_provider = create_provider(curator_config, curator_profile)
        # [2026-07-19] 所有新对象先完整校验构造，再一次替换轮次边界快照。
        self.controller.state_resolver = resolver
        self.controller.prompt_assembler = assembler
        self.controller.provider = chat_provider
        self.controller.curation_service.provider = curator_provider
        self.controller.curation_service.curator_persona = persona_prompts.memory_curator
        self.controller.curation_service.prompt_template = fresh.prompts["memory_curation"]
        self.controller.curation_service.config_revision = fresh.revision
        budget = settings["agent.toml"]["context_budget"]
        self.controller.memory_budgets = {
            "overview_chars": int(settings["agent.toml"]["memory_overview"]["max_chars"]),
            "long_term_chars": int(budget["long_term_tokens"]) * 4,
            "recent_chars": int(budget["short_term_tokens"]) * 4,
        }
        self.snapshot = fresh

    async def run_turn(self, content: str, *, resume_pending: bool = False, turn_id: str | None = None) -> str:
        self._reload_config()
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
        manual = settings["agent.toml"]["manual_memory"]
        long_term_store = LongTermStore(
            memory_root,
            archive,
            max_document_bytes=int(manual["max_document_bytes"]),
            max_items=int(manual["max_items"]),
            max_overview_chars=int(settings["agent.toml"]["memory_overview"]["max_chars"]),
        )
        long_term_store.initialize()
        permission = long_term_store.validate_permissions()
        if permission.status.value != "private":
            from bai_agent.domain.errors import BaiError

            raise BaiError(permission.error_code or "MEMORY_PERMISSION_INVALID", permission.warning or "长期记忆权限无效。")
        states_doc = settings["states.toml"]
        states = {str(item["id"]): tuple(item["ordered_persona_ids"]) for item in states_doc["states"] if item.get("enabled", False)}
        resolver = StaticStateResolver(snapshot.default_state_id, states)
        persona_prompts = PersonaPromptSet.from_snapshot(snapshot)
        state_prompts = persona_prompts.state_prompts(states[snapshot.default_state_id])
        assembler = PromptAssembler.mvp(persona_prompts.trusted_chat_instruction, state_prompts)
        managed_provider = provider is None
        if managed_provider:
            providers = settings["providers.toml"]
            chat_profile = providers["model_profiles"]["chat"]
            provider_config = next(item for item in providers["providers"] if item["id"] == chat_profile["provider"])
            provider = create_provider(provider_config, chat_profile)
            curator_profile = providers["model_profiles"]["memory_curator"]
            curator_config = next(
                item
                for item in providers["providers"]
                if item["id"] == curator_profile["provider"]
            )
            curator_provider = create_provider(curator_config, curator_profile)
        else:
            curator_provider = provider
        short = settings["agent.toml"]["short_term"]
        curation_service = CurationService(
            archive,
            long_term_store,
            curator_provider,
            CurationPolicy(
                max_records=int(short["max_records"]),
                reserved_records=int(short["reserved_records"]),
                min_batch_records=int(short["curation_batch_min_records"]),
                max_batch_records=int(short["curation_batch_max_records"]),
            ),
            curator_persona=persona_prompts.memory_curator,
            prompt_template=snapshot.prompts["memory_curation"],
            config_revision=snapshot.revision,
            tracer=tracer,
        )
        tool_config = next(item for item in settings["tools.toml"]["tools"] if item["id"] == "memory_source_query")
        source_tool = MemorySourceQueryTool(
            long_term_store,
            archive,
            page_size=int(tool_config["page_size"]),
            tracer=tracer,
        )
        registry = ToolRegistry()
        registry.register(
            source_tool.definition,
            source_tool,
            enabled=bool(tool_config["enabled"]),
            allowed_personas=tuple(tool_config["allowed_personas"]),
            read_only=bool(tool_config["read_only"]),
        )
        tool_executor = ToolExecutor(
            registry,
            deadline_seconds=float(settings["agent.toml"]["runtime"]["tool_deadline_seconds"]),
            max_result_bytes=int(tool_config["max_result_bytes"]),
            tracer=tracer,
        )
        budgets = settings["agent.toml"]["context_budget"]
        controller = SingleTurnController(
            archive,
            provider,
            resolver,
            assembler,
            on_output=on_output,
            tracer=tracer,
            long_term_store=long_term_store,
            curation_service=curation_service,
            tool_executor=tool_executor,
            max_tool_rounds=int(settings["agent.toml"]["runtime"]["max_tool_rounds"]),
            memory_budgets={
                "overview_chars": int(settings["agent.toml"]["memory_overview"]["max_chars"]),
                "long_term_chars": int(budgets["long_term_tokens"]) * 4,
                "recent_chars": int(budgets["short_term_tokens"]) * 4,
            },
            tool_definitions=tuple(
                definition.model_dump(mode="json")
                for definition in registry.definitions_for("chat")
            ),
        )
        return AgentApplication(
            snapshot,
            archive,
            long_term_store,
            controller,
            lease,
            config_dir=config_dir,
            managed_provider=managed_provider,
        )
    except Exception:
        lease.release()
        raise
