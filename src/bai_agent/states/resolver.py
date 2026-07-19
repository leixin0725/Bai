"""[2026-07-19] 首版状态解析只读取可信配置，永不解析用户、记忆或工具正文。"""

from __future__ import annotations

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import AgentStateDefinition, StateResolutionContext, StateResolutionResult


class StaticStateResolver:
    def __init__(self, default_state_id: str, states: dict[str, tuple[str, ...]]) -> None:
        if default_state_id not in states:
            raise BaiError("STATE_REFERENCE_MISSING", "默认状态引用不存在。")
        self.default_state_id = default_state_id
        self.states = {
            state_id: AgentStateDefinition(
                state_id=state_id,
                ordered_persona_ids=tuple(personas),
                enabled=True,
            )
            for state_id, personas in states.items()
        }
        self._selected_state_id = default_state_id

    @classmethod
    def default(cls) -> "StaticStateResolver":
        return cls("default", {"default": ("state_default",)})

    @classmethod
    def for_test(
        cls, state_id: str, states: dict[str, tuple[str, ...]]
    ) -> "StaticStateResolver":
        """[2026-07-19] 测试可选择已验证状态；生产装配始终使用默认状态。"""
        resolver = cls(next(iter(states)), states)
        if state_id not in resolver.states:
            raise BaiError("STATE_REFERENCE_MISSING", "测试状态引用不存在。")
        resolver._selected_state_id = state_id
        return resolver

    def resolve(self, context: StateResolutionContext) -> StateResolutionResult:
        del context  # [2026-07-19] 静态解析明确忽略不可信正文，只保留可替换接口。
        return StateResolutionResult(
            state_id=self._selected_state_id,
            ordered_persona_ids=self.states[self._selected_state_id].ordered_persona_ids,
            resolver_id="static",
            resolver_version="1",
            reason_code=(
                "configured_default"
                if self._selected_state_id == self.default_state_id
                else "test_selected_state"
            ),
        )
