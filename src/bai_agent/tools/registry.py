"""[2026-07-19] 所有人格共享同一工具注册表、Schema 和权限交集。"""

from __future__ import annotations

from dataclasses import dataclass

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import ToolDefinition


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    definition: ToolDefinition
    implementation: object
    enabled: bool
    allowed_personas: tuple[str, ...]
    read_only: bool
    compensation_contract: str | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        definition: ToolDefinition,
        implementation: object,
        *,
        enabled: bool,
        allowed_personas: tuple[str, ...],
        read_only: bool = True,
        compensation_contract: str | None = None,
    ) -> None:
        if definition.name in self._tools:
            raise BaiError("TOOL_DUPLICATE", "工具名称重复。")
        if not read_only:
            transactional = all(callable(getattr(implementation, name, None)) for name in ("prepare", "commit", "rollback"))
            compensating = bool(compensation_contract) and callable(getattr(implementation, "compensate", None))
            if not transactional and not compensating:
                raise BaiError("TOOL_RECOVERY_REQUIRED", "写工具缺少可恢复事务或明确补偿契约。")
        self._tools[definition.name] = RegisteredTool(
            definition, implementation, enabled, allowed_personas, read_only, compensation_contract
        )

    def resolve(self, name: str, persona_id: str) -> RegisteredTool:
        registered = self._tools.get(name)
        if registered is None:
            raise BaiError("TOOL_NOT_FOUND", "工具未注册。")
        if not registered.enabled:
            raise BaiError("TOOL_DISABLED", "工具未启用。")
        if "*" not in registered.allowed_personas and persona_id not in registered.allowed_personas:
            raise BaiError("TOOL_DENIED", "当前人格无权调用该工具。")
        return registered

    def definitions_for(self, persona_id: str) -> tuple[ToolDefinition, ...]:
        return tuple(
            item.definition
            for item in self._tools.values()
            if item.enabled
            and ("*" in item.allowed_personas or persona_id in item.allowed_personas)
        )
