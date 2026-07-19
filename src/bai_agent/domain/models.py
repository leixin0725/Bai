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
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CompletionResult(FrozenModel):
    text: str
    finish_reason: str
    tool_calls: tuple[dict[str, JsonValue], ...] = ()
    usage: dict[str, int] = Field(default_factory=dict)


class StateResolutionContext(FrozenModel):
    turn_id: str
    untrusted_text: str = ""


class StateResolutionResult(FrozenModel):
    state_id: str
    ordered_persona_ids: tuple[str, ...]
    resolver_id: str
    resolver_version: str
    reason_code: str


class PromptSegment(FrozenModel):
    segment_id: str
    trust: TrustLevel
    content: str
    source_ids: tuple[str, ...] = ()


class PromptContext(FrozenModel):
    flow_id: str
    turn_id: str
    config_revision: str
    state_resolution: StateResolutionResult
    segments: tuple[PromptSegment, ...]
    source_manifest: tuple[dict[str, str], ...] = ()
    coverage: dict[str, JsonValue] = Field(default_factory=dict)


class MemoryKind(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    CONSTRAINT = "constraint"
    EVENT = "event"
    TASK = "task"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class SourceRelation(StrEnum):
    SUPPORTS = "supports"
    CORRECTS = "corrects"
    SUPERSEDES = "supersedes"
    MANUAL_BASIS = "manual_basis"


class CreatedBy(StrEnum):
    MEMORY_CURATOR = "memory_curator"
    MANUAL = "manual"


class SourceReference(FrozenModel):
    record_id: str
    relation: SourceRelation
    record_sha256: str

    @field_validator("record_id")
    @classmethod
    def validate_record_id(cls, value: str) -> str:
        return _validate_prefixed_uuid(value, "rec")

    @field_validator("record_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("来源必须包含完整 sha256")
        int(value[7:], 16)
        return value


class CoverageSpan(FrozenModel):
    start_sequence: int = Field(ge=1)
    end_sequence: int = Field(ge=1)
    batch_id: str
    record_ids: tuple[str, ...]
    record_hashes: tuple[str, ...]

    @field_validator("batch_id")
    @classmethod
    def validate_batch_id(cls, value: str) -> str:
        return _validate_prefixed_uuid(value, "batch")

    @model_validator(mode="after")
    def validate_span(self) -> "CoverageSpan":
        expected = self.end_sequence - self.start_sequence + 1
        if expected <= 0 or len(self.record_ids) != expected or len(self.record_hashes) != expected:
            raise ValueError("coverage span 范围与记录数量不一致")
        if len(set(self.record_ids)) != len(self.record_ids):
            raise ValueError("coverage span 记录重复")
        for record_id in self.record_ids:
            _validate_prefixed_uuid(record_id, "rec")
        for digest in self.record_hashes:
            if not digest.startswith("sha256:") or len(digest) != 71:
                raise ValueError("coverage span 摘要无效")
            int(digest[7:], 16)
        return self


class MemoryCoverageOverview(FrozenModel):
    revision: int = Field(ge=0)
    text: str = Field(min_length=1)
    coverage_spans: tuple[CoverageSpan, ...] = ()

    @model_validator(mode="after")
    def validate_continuity(self) -> "MemoryCoverageOverview":
        expected = 1
        for span in self.coverage_spans:
            if span.start_sequence != expected:
                raise ValueError("coverage span 存在缺口或重叠")
            expected = span.end_sequence + 1
        return self

    @classmethod
    def empty(cls) -> "MemoryCoverageOverview":
        return cls(revision=0, text="尚无已整理记录。", coverage_spans=())


class CurationCheckpoint(FrozenModel):
    curated_through_sequence: int = Field(ge=0)
    last_batch_id: str | None
    updated_at: datetime | None
    covered_record_ids: tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> "CurationCheckpoint":
        return cls(curated_through_sequence=0, last_batch_id=None, updated_at=None, covered_record_ids=())


class LongTermMemoryItem(FrozenModel):
    memory_id: str
    kind: MemoryKind
    text: str = Field(min_length=1, max_length=8192)
    status: MemoryStatus
    source_refs: tuple[SourceReference, ...] = Field(min_length=1)
    created_by: CreatedBy
    created_at: datetime
    updated_at: datetime
    supersedes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @field_validator("memory_id")
    @classmethod
    def validate_memory_id(cls, value: str) -> str:
        return _validate_prefixed_uuid(value, "mem")

    @model_validator(mode="after")
    def validate_item(self) -> "LongTermMemoryItem":
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("长期记忆时间必须包含时区")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        if self.memory_id in self.supersedes or len(set(self.supersedes)) != len(self.supersedes):
            raise ValueError("supersedes 不能自指或重复")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tags 不能重复")
        return self

    @property
    def is_current(self) -> bool:
        return self.status == MemoryStatus.ACTIVE


class LongTermMemoryDocument(FrozenModel):
    schema_version: int = 1
    revision: int = Field(ge=0)
    curation: CurationCheckpoint
    coverage_overview: MemoryCoverageOverview
    memories: tuple[LongTermMemoryItem, ...] = ()

    @model_validator(mode="after")
    def validate_document(self) -> "LongTermMemoryDocument":
        if self.schema_version != 1:
            raise ValueError("长期记忆 Schema 版本不受支持")
        if self.coverage_overview.revision != self.revision:
            raise ValueError("覆盖概览 revision 必须与文档一致")
        frontier = self.curation.curated_through_sequence
        last_end = self.coverage_overview.coverage_spans[-1].end_sequence if self.coverage_overview.coverage_spans else 0
        if last_end != frontier:
            raise ValueError("覆盖概览必须连续覆盖到整理前沿")
        ids = {item.memory_id for item in self.memories}
        if len(ids) != len(self.memories):
            raise ValueError("长期记忆 ID 重复")
        graph: dict[str, tuple[str, ...]] = {}
        for item in self.memories:
            if any(target not in ids for target in item.supersedes):
                raise ValueError("supersedes 存在悬空引用")
            graph[item.memory_id] = item.supersedes
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(memory_id: str) -> None:
            if memory_id in visiting:
                raise ValueError("supersedes 存在关系环")
            if memory_id in visited:
                return
            visiting.add(memory_id)
            for target in graph[memory_id]:
                visit(target)
            visiting.remove(memory_id)
            visited.add(memory_id)

        for memory_id in graph:
            visit(memory_id)
        return self

    @classmethod
    def empty(cls) -> "LongTermMemoryDocument":
        return cls(
            schema_version=1,
            revision=0,
            curation=CurationCheckpoint.empty(),
            coverage_overview=MemoryCoverageOverview.empty(),
            memories=(),
        )


class CurationBatch(FrozenModel):
    batch_id: str
    old_frontier: int = Field(ge=0)
    new_frontier: int = Field(ge=1)
    record_ids: tuple[str, ...] = Field(min_length=1)
    config_revision: str
    content_sha256: str


class CurationCandidate(FrozenModel):
    kind: MemoryKind
    text: str = Field(min_length=1, max_length=8192)
    source_record_ids: tuple[str, ...] = Field(min_length=1)
    tags: tuple[str, ...] = ()


class OverviewUpdate(FrozenModel):
    text: str = Field(min_length=1)
    record_ids: tuple[str, ...] = Field(min_length=1)


class CurationProposal(FrozenModel):
    memory_candidates: tuple[CurationCandidate, ...]
    overview_update: OverviewUpdate


class ToolOutcome(StrEnum):
    SUCCESS = "success"
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_FOUND = "not_found"
    DENIED = "denied"
    TIMEOUT = "timeout"
    EXECUTION_FAILURE = "execution_failure"


class ToolDefinition(FrozenModel):
    tool_id: str
    name: str
    description: str
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    safety: dict[str, JsonValue] = Field(default_factory=dict)


class ToolExecutionContext(FrozenModel):
    flow_id: str
    turn_id: str
    persona_id: str
    state_id: str
    config_revision: str
    trigger_record_id: str | None = None


class ToolCall(FrozenModel):
    call_id: str
    name: str
    arguments: dict[str, JsonValue]


class ToolResult(FrozenModel):
    call_id: str
    outcome: ToolOutcome
    data: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str | None = None


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
