"""[2026-07-19] 应用装配只连接配置快照、领域端口和适配器，不复制业务规则。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bai_agent.config.loader import describe_config_error, load_config
from bai_agent.debug.tui import TextualApprovalPresenter
from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    ReloadStatus,
    SourceKind,
    SourceRef,
    TemporalSegmentationPolicy,
)
from bai_agent.domain.ports import SystemClock
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.curation import CurationPolicy, CurationService
from bai_agent.memory.long_term import LongTermStore
from bai_agent.memory.recovery import WriterLease
from bai_agent.memory.transaction import TurnUnitOfWork
from bai_agent.model_calls.gateway import CallIdentityAllocator, LegacyProviderAdapter, ModelCallGateway
from bai_agent.model_calls.estimation import create_estimator
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.prompting.boundaries import UntrustedBoundaryRenderer
from bai_agent.prompting.personas import PersonaPromptSet
from bai_agent.providers.registry import create_provider
from bai_agent.runtime.controller import SingleTurnController
from bai_agent.states.resolver import StaticStateResolver
from bai_agent.tools.executor import ToolExecutor
from bai_agent.tools.memory_source import MemorySourceQueryTool
from bai_agent.tools.registry import ToolRegistry


def _temporal_policy(snapshot) -> TemporalSegmentationPolicy:
    """[2026-07-20] 时间策略只由同一已验证快照及其真实配置资产构造。"""
    settings = snapshot.settings["history_timestamps.toml"]
    asset = next(
        item for item in snapshot.assets if item.asset_id == "config:history_timestamps"
    )
    source = SourceRef(
        source_kind=SourceKind.CONFIG_FILE,
        source_id=asset.asset_id,
        project_relative_path=f"config/{asset.project_relative_path}",
        content_sha256=asset.content_sha256,
        revision=snapshot.revision,
        producer="config_loader",
    )
    zone_name = str(settings["display_timezone"])
    return TemporalSegmentationPolicy(
        display_timezone=ZoneInfo(zone_name),
        display_timezone_name=zone_name,
        long_gap=timedelta(minutes=int(settings["long_gap_minutes"])),
        continuous_refresh=timedelta(
            minutes=int(settings["continuous_segment_refresh_minutes"])
        ),
        split_on_local_date_change=bool(settings["split_on_local_date_change"]),
        config_source=source,
    )


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
        debug_prompts: bool,
        input_source=None,
    ) -> None:
        self.snapshot = snapshot
        self.archive = archive
        self.long_term_store = long_term_store
        self.controller = controller
        self.lease = lease
        self.config_dir = config_dir
        self.managed_provider = managed_provider
        self.debug_prompts = debug_prompts
        self._input_source = input_source
        self._config_reload_required = False

    def _reload_config(self) -> None:
        try:
            fresh = load_config(self.config_dir, require_credentials=self.managed_provider)
        except Exception:
            self._config_reload_required = True
            raise
        if fresh.revision == self.snapshot.revision and not self._config_reload_required:
            return
        # [2026-08-01] A failed reload must rebuild once after repair, even when
        # the repaired files exactly match the last published revision.
        self._config_reload_required = True
        settings = fresh.settings
        states_doc = settings["states.toml"]
        states = {
            str(item["id"]): tuple(item["ordered_persona_ids"])
            for item in states_doc["states"]
            if item.get("enabled", False)
        }
        resolver = StaticStateResolver(fresh.default_state_id, states)
        persona_prompts = PersonaPromptSet.from_snapshot(fresh)
        assets = {item.asset_id: item for item in fresh.assets}
        state_asset_values = tuple(
            assets[f"persona:{persona_id}"]
            for persona_id in states[fresh.default_state_id]
            if f"persona:{persona_id}" in assets
        )
        temporal_policy = _temporal_policy(fresh)
        boundary_renderer = UntrustedBoundaryRenderer(
            assets["prompt:untrusted_memory_boundary"]
        )
        assembler = PromptAssembler.mvp(
            persona_prompts.chat,
            persona_prompts.state_prompts(states[fresh.default_state_id]),
            base_asset=assets.get("persona:chat"),
            state_assets=state_asset_values,
            temporal_policy=temporal_policy,
            boundary_renderer=boundary_renderer,
        )
        chat_provider = self.controller.provider
        curator_provider = self.controller.curation_service.provider
        if self.managed_provider:
            identity_allocator = self.controller.provider.identity_allocator
            providers = settings["providers.toml"]
            chat_profile = providers["model_profiles"]["chat"]
            chat_config = next(
                item for item in providers["providers"] if item["id"] == chat_profile["provider"]
            )
            chat_adapter = create_provider(chat_config, chat_profile)
            chat_estimator = create_estimator(chat_config, chat_profile, settings["agent.toml"]["debug_prompt"])
            chat_provider = ModelCallGateway(
                chat_adapter,
                debug_enabled=self.debug_prompts,
                presenter=(TextualApprovalPresenter(color_policy=str(settings["agent.toml"]["debug_prompt"]["color"]), input_source=self._input_source) if self.debug_prompts else None),
                max_attempts=int(chat_config.get("retry", {}).get("max_attempts", 1)),
                identity_allocator=identity_allocator,
                clock=self.controller.clock,
                estimator=chat_estimator,
            )
            curator_profile = providers["model_profiles"]["memory_curator"]
            curator_config = next(
                item for item in providers["providers"] if item["id"] == curator_profile["provider"]
            )
            curator_adapter = create_provider(curator_config, curator_profile)
            curator_estimator = create_estimator(curator_config, curator_profile, settings["agent.toml"]["debug_prompt"])
            curator_provider = ModelCallGateway(
                curator_adapter,
                debug_enabled=self.debug_prompts,
                presenter=(TextualApprovalPresenter(color_policy=str(settings["agent.toml"]["debug_prompt"]["color"]), input_source=self._input_source) if self.debug_prompts else None),
                max_attempts=int(curator_config.get("retry", {}).get("max_attempts", 1)),
                identity_allocator=identity_allocator,
                clock=self.controller.clock,
                estimator=curator_estimator,
            )
        short = settings["agent.toml"]["short_term"]
        curation_service = CurationService(
            self.archive,
            self.long_term_store,
            curator_provider,
            CurationPolicy(
                max_records=int(short["max_records"]),
                reserved_records=int(short["reserved_records"]),
                min_batch_records=int(short["curation_batch_min_records"]),
                max_batch_records=int(short["curation_batch_max_records"]),
            ),
            curator_persona=persona_prompts.memory_curator,
            prompt_template=fresh.prompts["memory_curation"],
            config_revision=fresh.revision,
            temporal_policy=temporal_policy,
            boundary_renderer=boundary_renderer,
            curator_asset=assets.get("persona:memory_curator"),
            prompt_asset=assets.get("prompt:memory_curation"),
        )
        tool_config = next(
            item for item in settings["tools.toml"]["tools"]
            if item["id"] == "memory_source_query"
        )
        source_tool = MemorySourceQueryTool(
            self.long_term_store,
            self.archive,
            page_size=int(tool_config["page_size"]),
        )
        registry = ToolRegistry()
        registry.register(
            source_tool.definition,
            source_tool,
            enabled=bool(tool_config["enabled"]),
            allowed_personas=tuple(tool_config["allowed_personas"]),
            read_only=bool(tool_config["read_only"]),
            source_refs=(
                SourceRef(
                    source_kind=SourceKind.CONFIG_FILE,
                    source_id="config:tools",
                    project_relative_path="config/tools.toml",
                    content_sha256=assets["config:tools"].content_sha256,
                    revision=fresh.revision,
                    entity_ids=(source_tool.definition.tool_id,),
                    producer="config_loader",
                ),
            ),
        )
        tool_executor = ToolExecutor(
            registry,
            deadline_seconds=float(settings["agent.toml"]["runtime"]["tool_deadline_seconds"]),
            max_result_bytes=int(tool_config["max_result_bytes"]),
            clock=self.controller.clock,
        )
        budget = settings["agent.toml"]["context_budget"]
        memory_budgets = {
            "overview_chars": int(settings["agent.toml"]["memory_overview"]["max_chars"]),
            "long_term_chars": int(budget["long_term_tokens"]) * 4,
            "recent_chars": int(budget["short_term_tokens"]) * 4,
        }
        # [2026-07-20] 所有消费者及有副作用边界先在局部完整构造，再以一个引用替换发布。
        controller = SingleTurnController(
            self.archive,
            chat_provider,
            resolver,
            assembler,
            on_output=self.controller.on_output,
            long_term_store=self.long_term_store,
            curation_service=curation_service,
            tool_executor=tool_executor,
            max_tool_rounds=int(settings["agent.toml"]["runtime"]["max_tool_rounds"]),
            memory_budgets=memory_budgets,
            tool_definitions=tuple(
                definition.model_dump(mode="json")
                for definition in registry.definitions_for("chat")
            ),
            transaction_root=self.controller.transaction_root,
            temporal_policy=temporal_policy,
            clock=self.controller.clock,
        )
        self.controller = controller
        self.snapshot = fresh
        self._config_reload_required = False

    async def run_turn(
        self,
        content: str,
        *,
        resume_pending: bool = False,
        turn_id: str | None = None,
        reload_config: bool = True,
    ) -> str:
        """[2026-08-08] reload_config=False 供运行时外壳在重载尝试后复用旧快照。"""
        if reload_config:
            self._reload_config()
        return await self.controller.run_turn(
            content,
            resume_pending=resume_pending,
            turn_id=turn_id,
            config_revision=self.snapshot.revision,
        )

    def reload_config_with_status(self) -> ReloadStatus:
        """[2026-08-08] 重载结果带分组/字段/原因定位，供外壳警告与状态快照使用。"""
        revision = self.snapshot.revision
        try:
            self._reload_config()
        except BaiError as exc:
            return ReloadStatus(revision=revision, ok=False, error=describe_config_error(exc))
        return ReloadStatus(revision=self.snapshot.revision, ok=True, error=None)

    def discard_pending(self, expected_turn_id: str | None = None) -> str | None:
        """[2026-07-20] CLI 只能通过统一控制器放弃已校验的 raw 尾部 pending。"""
        return self.controller.discard_pending(expected_turn_id=expected_turn_id)

    def close(self) -> None:
        self.lease.release()


def build_application(
    config_dir: Path,
    data_dir: Path | None = None,
    *,
    provider: Any | None = None,
    on_output=None,
    debug_prompts: bool = False,
    presenter=None,
    clock=None,
    input_source=None,
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
        # [2026-07-20] 持锁恢复必须先于 pending 读取、Provider 创建和任何新轮输入。
        TurnUnitOfWork(memory_root, archive, long_term_store).recover()
        states_doc = settings["states.toml"]
        states = {str(item["id"]): tuple(item["ordered_persona_ids"]) for item in states_doc["states"] if item.get("enabled", False)}
        resolver = StaticStateResolver(snapshot.default_state_id, states)
        persona_prompts = PersonaPromptSet.from_snapshot(snapshot)
        state_prompts = persona_prompts.state_prompts(states[snapshot.default_state_id])
        assets = {item.asset_id: item for item in snapshot.assets}
        temporal_policy = _temporal_policy(snapshot)
        boundary_renderer = UntrustedBoundaryRenderer(
            assets["prompt:untrusted_memory_boundary"]
        )
        assembler = PromptAssembler.mvp(
            persona_prompts.chat,
            state_prompts,
            base_asset=assets.get("persona:chat"),
            state_assets=tuple(
                assets[f"persona:{persona_id}"]
                for persona_id in states[snapshot.default_state_id]
                if f"persona:{persona_id}" in assets
            ),
            temporal_policy=temporal_policy,
            boundary_renderer=boundary_renderer,
        )
        managed_provider = provider is None
        if managed_provider:
            providers = settings["providers.toml"]
            chat_profile = providers["model_profiles"]["chat"]
            provider_config = next(item for item in providers["providers"] if item["id"] == chat_profile["provider"])
            chat_adapter = create_provider(provider_config, chat_profile)
            chat_estimator = create_estimator(provider_config, chat_profile, settings["agent.toml"]["debug_prompt"])
            curator_profile = providers["model_profiles"]["memory_curator"]
            curator_config = next(
                item
                for item in providers["providers"]
                if item["id"] == curator_profile["provider"]
            )
            curator_adapter = create_provider(curator_config, curator_profile)
            curator_estimator = create_estimator(curator_config, curator_profile, settings["agent.toml"]["debug_prompt"])
        else:
            chat_adapter = provider if all(hasattr(provider, name) for name in ("prepare", "materialize_sdk_kwargs", "send_once")) else LegacyProviderAdapter(provider, {"model": "test-model", "max_output_tokens": 8192})
            curator_adapter = chat_adapter
            provider_config = {"retry": {"max_attempts": 1}}
            curator_config = provider_config
            chat_estimator = None
            curator_estimator = None
        color_policy = str(settings["agent.toml"]["debug_prompt"]["color"])
        clock = clock or SystemClock()
        identity_allocator = CallIdentityAllocator()
        chat_presenter = presenter or (TextualApprovalPresenter(color_policy=color_policy, input_source=input_source) if debug_prompts else None)
        curator_presenter = presenter or (TextualApprovalPresenter(color_policy=color_policy, input_source=input_source) if debug_prompts else None)
        provider = ModelCallGateway(
            chat_adapter,
            debug_enabled=debug_prompts,
            presenter=chat_presenter,
            max_attempts=int(provider_config.get("retry", {}).get("max_attempts", 1)),
            identity_allocator=identity_allocator,
            clock=clock,
            estimator=chat_estimator,
        )
        curator_provider = ModelCallGateway(
            curator_adapter,
            debug_enabled=debug_prompts,
            presenter=curator_presenter,
            max_attempts=int(curator_config.get("retry", {}).get("max_attempts", 1)),
            identity_allocator=identity_allocator,
            clock=clock,
            estimator=curator_estimator,
        )
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
            temporal_policy=temporal_policy,
            boundary_renderer=boundary_renderer,
            curator_asset=assets.get("persona:memory_curator"),
            prompt_asset=assets.get("prompt:memory_curation"),
        )
        tool_config = next(item for item in settings["tools.toml"]["tools"] if item["id"] == "memory_source_query")
        source_tool = MemorySourceQueryTool(
            long_term_store,
            archive,
            page_size=int(tool_config["page_size"]),
        )
        registry = ToolRegistry()
        registry.register(
            source_tool.definition,
            source_tool,
            enabled=bool(tool_config["enabled"]),
            allowed_personas=tuple(tool_config["allowed_personas"]),
            read_only=bool(tool_config["read_only"]),
            source_refs=(
                SourceRef(
                    source_kind=SourceKind.CONFIG_FILE,
                    source_id="config:tools",
                    project_relative_path="config/tools.toml",
                    content_sha256=assets["config:tools"].content_sha256,
                    revision=snapshot.revision,
                    entity_ids=(source_tool.definition.tool_id,),
                    producer="config_loader",
                ),
            ),
        )
        tool_executor = ToolExecutor(
            registry,
            deadline_seconds=float(settings["agent.toml"]["runtime"]["tool_deadline_seconds"]),
            max_result_bytes=int(tool_config["max_result_bytes"]),
            clock=clock,
        )
        budgets = settings["agent.toml"]["context_budget"]
        controller = SingleTurnController(
            archive,
            provider,
            resolver,
            assembler,
            on_output=on_output,
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
            transaction_root=memory_root,
            temporal_policy=temporal_policy,
            clock=clock,
        )
        return AgentApplication(
            snapshot,
            archive,
            long_term_store,
            controller,
            lease,
            config_dir=config_dir,
            managed_provider=managed_provider,
            debug_prompts=debug_prompts,
            input_source=input_source,
        )
    except Exception:
        lease.release()
        raise
