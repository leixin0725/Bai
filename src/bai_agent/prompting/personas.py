"""[2026-07-19] 人格解析按职责返回可信指令；缺少任何引用时立即失败。"""

from __future__ import annotations

from dataclasses import dataclass

from bai_agent.domain.errors import BaiError


@dataclass(frozen=True, slots=True)
class PersonaPromptSet:
    chat: str
    memory_curator: str
    states: dict[str, str]
    untrusted_boundary: str

    @classmethod
    def from_snapshot(cls, snapshot) -> "PersonaPromptSet":
        by_role = {item.role: [] for item in snapshot.personas}
        for item in snapshot.personas:
            by_role.setdefault(item.role, []).append(item)
        if len(by_role.get("chat", [])) != 1 or len(by_role.get("memory_curator", [])) != 1:
            raise BaiError("PERSONA_ROLE_INVALID", "聊天和记忆整理人格必须各有唯一配置。")
        boundary = snapshot.prompts.get("untrusted_memory_boundary")
        if not boundary:
            raise BaiError("PROMPT_SEGMENT_MISSING", "不可信数据边界提示缺失。")
        return cls(
            chat=by_role["chat"][0].prompt,
            memory_curator=by_role["memory_curator"][0].prompt,
            states={item.persona_id: item.prompt for item in by_role.get("state", [])},
            untrusted_boundary=boundary,
        )

    def state_prompts(self, persona_ids: tuple[str, ...]) -> tuple[str, ...]:
        try:
            return tuple(self.states[persona_id] for persona_id in persona_ids)
        except KeyError as exc:
            raise BaiError("STATE_PERSONA_MISSING", "状态人格引用不存在。") from exc

    @property
    def trusted_chat_instruction(self) -> str:
        return f"{self.chat.rstrip()}\n\n{self.untrusted_boundary.strip()}"
