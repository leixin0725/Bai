"""[2026-07-19] 领域对象保持不可变、可校验，并可脱离供应商 SDK 序列化。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# [2026-07-19] JSON 的递归合法性在 Pydantic/Schema 边界校验；别名避免解释器递归展开。
JsonScalar = str | int | float | bool | None
JsonValue = Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError("ID 前缀无效")
    return f"{prefix}-{uuid4()}"


def canonical_json(value: JsonValue | Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def content_hash(content: str | bytes) -> str:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return "sha256:" + sha256(payload).hexdigest()


def _validate_prefixed_uuid(value: str, prefix: str) -> str:
    marker = f"{prefix}-"
    if not value.startswith(marker):
        raise ValueError(f"必须使用 {prefix} ID")
    UUID(value[len(marker) :])
    return value


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class TrustLevel(StrEnum):
    TRUSTED_INSTRUCTION = "trusted_instruction"
    USER_INSTRUCTION = "user_instruction"
    UNTRUSTED_DATA = "untrusted_data"


class Message(FrozenModel):
    role: str
    content: str = Field(min_length=1)
    trust: TrustLevel = TrustLevel.UNTRUSTED_DATA


class RawRecord(FrozenModel):
    schema_version: int = 1
    record_id: str
    global_sequence: int = Field(ge=1)
    turn_id: str
    role: Role
    content: str = Field(min_length=1)
    created_at: datetime
    state_id: str = Field(min_length=1)
    config_revision: str
    content_sha256: str

    @field_validator("record_id")
    @classmethod
    def validate_record_id(cls, value: str) -> str:
        return _validate_prefixed_uuid(value, "rec")

    @field_validator("turn_id")
    @classmethod
    def validate_turn_id(cls, value: str) -> str:
        return _validate_prefixed_uuid(value, "turn")

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须包含 UTC 时区")
        return value.astimezone(timezone.utc)

    @field_validator("config_revision", "content_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("必须是完整 sha256 摘要")
        int(value[7:], 16)
        return value

    @model_validator(mode="after")
    def validate_content_digest(self) -> "RawRecord":
        if self.content_sha256 != content_hash(self.content):
            raise ValueError("正文摘要不匹配")
        return self

    @classmethod
    def create(cls, **values: Any) -> "RawRecord":
        values["content_sha256"] = content_hash(values["content"])
        return cls(**values)


class CompletionRequest(FrozenModel):
    flow_id: str
    turn_id: str
    messages: tuple[Message, ...] = ()
    tool_definitions: tuple[dict[str, JsonValue], ...] = ()
    model_profile_id: str = ""
    deadline_seconds: float | None = None


class CompletionResult(FrozenModel):
    text: str
    finish_reason: str
    tool_calls: tuple[dict[str, JsonValue], ...] = ()
    usage: dict[str, int] = Field(default_factory=dict)


class StateResolutionContext(FrozenModel):
    turn_id: str


class StateResolutionResult(FrozenModel):
    state_id: str
    ordered_persona_ids: tuple[str, ...]
    resolver_id: str
    resolver_version: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class PersonaProfile:
    persona_id: str
    role: str
    prompt_path: str
    prompt: str
    model_profile_id: str
    allowed_tool_ids: tuple[str, ...] = ()


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    revision: str
    config_root: str
    data_root: str
    default_state_id: str
    personas: tuple[PersonaProfile, ...]
    prompts: Mapping[str, str]
    settings: Mapping[str, Any]

    @classmethod
    def create(cls, **values: Any) -> "ConfigSnapshot":
        values["prompts"] = _freeze(dict(values["prompts"]))
        values["settings"] = _freeze(dict(values["settings"]))
        return cls(**values)
