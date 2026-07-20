"""[2026-07-19] 领域对象保持不可变、可校验，并可脱离供应商 SDK 序列化。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import PurePosixPath
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


def freeze_json(value: Any) -> Any:
    """[2026-07-20] 递归冻结唯一物化载荷，禁止批准后嵌套字段发生突变。"""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("物化载荷只能包含 JSON 值")


def thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


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
    content: str
    trust: TrustLevel = TrustLevel.UNTRUSTED_DATA
    tool_call_id: str | None = None


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
    usage_unavailable_reason: str | None = None


class StateResolutionContext(FrozenModel):
    turn_id: str
    untrusted_text: str = ""


class StateResolutionResult(FrozenModel):
    state_id: str
    ordered_persona_ids: tuple[str, ...]
    resolver_id: str
    resolver_version: str
    reason_code: str

    @field_validator("ordered_persona_ids")
    @classmethod
    def validate_persona_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("状态人格 ID 不能重复")
        return value


class AgentStateDefinition(FrozenModel):
    state_id: str = Field(min_length=1)
    ordered_persona_ids: tuple[str, ...] = ()
    enabled: bool

    @field_validator("ordered_persona_ids")
    @classmethod
    def validate_persona_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("状态人格 ID 不能重复")
        if any(not item for item in value):
            raise ValueError("状态人格 ID 不能为空")
        return value


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
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
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


class SourceKind(StrEnum):
    CONFIG_FILE = "config_file"
    DATA_FILE = "data_file"
    RUNTIME = "runtime"
    GENERATED = "generated"


class Participation(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    EMPTY = "empty"
    UNKNOWN_SOURCE = "unknown_source"


class TransactionState(StrEnum):
    PREPARED = "PREPARED"
    READY_PENDING = "READY_PENDING"
    READY_TO_COMMIT = "READY_TO_COMMIT"


class ApprovalValue(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ConfigAsset(FrozenModel):
    """[2026-07-20] 配置资产绑定实际加载内容，后续文件修改不能改变本次来源。"""

    asset_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    project_relative_path: str = Field(min_length=1)
    content: str
    content_sha256: str
    revision: str

    @model_validator(mode="after")
    def validate_identity(self) -> "ConfigAsset":
        path = PurePosixPath(self.project_relative_path.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or self.project_relative_path.startswith(("/", "\\")):
            raise ValueError("配置资产路径必须位于项目内")
        if self.content_sha256 != content_hash(self.content):
            raise ValueError("配置资产正文摘要不匹配")
        if not self.revision.startswith("sha256:") or len(self.revision) != 71:
            raise ValueError("配置资产 revision 无效")
        return self


class SourceRef(FrozenModel):
    source_kind: SourceKind
    source_id: str = Field(min_length=1)
    project_relative_path: str | None = None
    content_sha256: str | None = None
    revision: str | None = None
    entity_ids: tuple[str, ...] = ()
    producer: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source(self) -> "SourceRef":
        if self.source_kind in {SourceKind.CONFIG_FILE, SourceKind.DATA_FILE}:
            if not self.project_relative_path or not self.content_sha256:
                raise ValueError("文件来源必须包含路径与内容摘要")
            path = PurePosixPath(self.project_relative_path.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("来源路径必须是项目相对路径")
        elif self.project_relative_path is not None:
            raise ValueError("运行时或生成来源不得伪造文件路径")
        if self.source_kind == SourceKind.RUNTIME and not self.entity_ids:
            raise ValueError("运行时来源必须包含关联标识")
        return self


class RequestPart(FrozenModel):
    part_id: str = Field(min_length=1)
    order: int = Field(ge=0)
    participation: Participation
    trust: TrustLevel
    payload_pointer: str | None = None
    text_span: tuple[int, int] | None = None
    content: str
    sources: tuple[SourceRef, ...] = ()
    exclusion_reason: str | None = None

    @model_validator(mode="after")
    def validate_part(self) -> "RequestPart":
        if self.participation == Participation.INCLUDED:
            if not self.payload_pointer or not self.sources:
                raise ValueError("参与请求的片段必须包含 pointer 与来源")
            if self.text_span is not None and not (0 <= self.text_span[0] <= self.text_span[1]):
                raise ValueError("正文区间无效")
        elif self.participation == Participation.UNKNOWN_SOURCE and self.sources:
            raise ValueError("未知来源片段不能带伪造来源")
        elif not self.exclusion_reason and self.participation != Participation.EMPTY:
            raise ValueError("非参与片段必须说明原因")
        return self


class ModelCallDraft(FrozenModel):
    call_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    flow_id: str = Field(min_length=1)
    call_sequence: int = Field(default=0, ge=0)
    purpose: str = Field(min_length=1)
    persona_id: str | None = None
    state_id: str | None = None
    config_revision: str
    model_profile_id: str
    request: CompletionRequest
    parts: tuple[RequestPart, ...]

    @model_validator(mode="after")
    def validate_parts(self) -> "ModelCallDraft":
        orders = [item.order for item in self.parts]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            raise ValueError("调用片段顺序必须严格且唯一")
        return self


class PreparedProviderRequest(FrozenModel):
    call_id: str
    attempt: int = Field(ge=1)
    provider_id: str
    model: str
    provider_request: Any
    max_output_tokens: int = Field(gt=0)
    parts: tuple[RequestPart, ...]
    call_sequence: int = Field(ge=1)
    purpose: str
    turn_id: str
    flow_id: str
    persona_id: str | None = None
    state_id: str | None = None
    config_revision: str

    @field_validator("provider_request", mode="before")
    @classmethod
    def freeze_provider_request(cls, value: Any) -> Any:
        return freeze_json(value)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class MaterializedSendPayload:
    """[2026-07-20] TUI 与 sender 共享这一份无认证、深度不可变发送载荷。"""

    call_id: str
    attempt: int
    provider_id: str
    model: str
    sdk_kwargs: Mapping[str, Any]
    canonical_payload_sha256: str

    @classmethod
    def create(
        cls,
        *,
        call_id: str,
        attempt: int,
        provider_id: str,
        model: str,
        sdk_kwargs: Mapping[str, Any],
    ) -> "MaterializedSendPayload":
        frozen = freeze_json(sdk_kwargs)
        if not isinstance(frozen, Mapping):
            raise ValueError("SDK 参数必须是映射")
        digest = content_hash(canonical_json(thaw_json(frozen)))
        return cls(call_id, attempt, provider_id, model, frozen, digest)

    def verify_digest(self) -> None:
        if content_hash(canonical_json(thaw_json(self.sdk_kwargs))) != self.canonical_payload_sha256:
            raise ValueError("物化载荷摘要不匹配")


class ApprovalDecision(FrozenModel):
    decision: ApprovalValue
    call_id: str
    attempt: int = Field(ge=1)
    payload_sha256: str
    decided_at: float

    @classmethod
    def approve(cls, payload: MaterializedSendPayload, *, decided_at: float = 0.0) -> "ApprovalDecision":
        return cls(
            decision=ApprovalValue.APPROVE,
            call_id=payload.call_id,
            attempt=payload.attempt,
            payload_sha256=payload.canonical_payload_sha256,
            decided_at=decided_at,
        )

    @classmethod
    def reject(cls, payload: MaterializedSendPayload, *, decided_at: float = 0.0) -> "ApprovalDecision":
        return cls(
            decision=ApprovalValue.REJECT,
            call_id=payload.call_id,
            attempt=payload.attempt,
            payload_sha256=payload.canonical_payload_sha256,
            decided_at=decided_at,
        )

    def validate_payload(self, payload: MaterializedSendPayload) -> None:
        payload.verify_digest()
        if (
            self.call_id != payload.call_id
            or self.attempt != payload.attempt
            or self.payload_sha256 != payload.canonical_payload_sha256
        ):
            raise ValueError("批准令牌与当前物化载荷不匹配")


class ContextUsageEstimate(FrozenModel):
    status: str
    estimated_input_tokens: int | None = Field(default=None, ge=0)
    part_tokens: dict[str, int] = Field(default_factory=dict)
    protocol_overhead_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int = Field(ge=0)
    projected_peak_tokens: int | None = Field(default=None, ge=0)
    context_capacity: int | None = Field(default=None, gt=0)
    projected_percent: float | None = None
    projected_remaining_tokens: int | None = None
    risk: str | None = None
    method: str | None = None
    confidence: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_conservation(self) -> "ContextUsageEstimate":
        if self.status == "estimated":
            if self.estimated_input_tokens is None or self.protocol_overhead_tokens is None:
                raise ValueError("估算状态必须包含输入与协议开销")
            if self.estimated_input_tokens != sum(self.part_tokens.values()) + self.protocol_overhead_tokens:
                raise ValueError("输入估算不守恒")
            if self.projected_peak_tokens != self.estimated_input_tokens + self.max_output_tokens:
                raise ValueError("峰值估算不守恒")
        elif not self.reason:
            raise ValueError("不可估算状态必须说明原因")
        return self


class ActualUsageSummary(FrozenModel):
    status: str
    actual_input_tokens: int | None = Field(default=None, ge=0)
    actual_output_tokens: int | None = Field(default=None, ge=0)
    actual_total_tokens: int | None = Field(default=None, ge=0)
    actual_percent: float | None = None
    estimated_input_tokens: int | None = Field(default=None, ge=0)
    input_estimation_error: int | None = None
    reason: str | None = None

    @classmethod
    def actual(
        cls,
        *,
        input_tokens: int,
        output_tokens: int,
        context_capacity: int | None,
        estimated_input_tokens: int | None,
    ) -> "ActualUsageSummary":
        total = input_tokens + output_tokens
        return cls(
            status="actual",
            actual_input_tokens=input_tokens,
            actual_output_tokens=output_tokens,
            actual_total_tokens=total,
            actual_percent=(total / context_capacity * 100 if context_capacity else None),
            estimated_input_tokens=estimated_input_tokens,
            input_estimation_error=(input_tokens - estimated_input_tokens if estimated_input_tokens is not None else None),
        )

    @classmethod
    def unavailable(cls, reason: str) -> "ActualUsageSummary":
        return cls(status="unavailable", reason=reason)


class PreTurnCheckpoint(FrozenModel):
    raw_count: int = Field(ge=0)
    raw_tail_id: str | None = None
    raw_sha256: str
    long_term_revision: int | None = Field(default=None, ge=0)
    long_term_sha256: str | None = None
    agent_state: str

    @classmethod
    def capture(cls, archive: Any, long_term_store: Any | None, agent_state: str) -> "PreTurnCheckpoint":
        records = archive.read_all()
        raw_identity = [
            {"record_id": item.record_id, "content_sha256": item.content_sha256}
            for item in records
        ]
        document = long_term_store.load() if long_term_store is not None else None
        return cls(
            raw_count=len(records),
            raw_tail_id=records[-1].record_id if records else None,
            raw_sha256=content_hash(canonical_json(raw_identity)),
            long_term_revision=document.revision if document is not None else None,
            long_term_sha256=(content_hash(canonical_json(document.model_dump(mode="json"))) if document is not None else None),
            agent_state=agent_state,
        )


class TurnWorkingSet(FrozenModel):
    checkpoint: PreTurnCheckpoint
    provisional_user_record: RawRecord
    curation_proposal: Any | None = None
    tool_results: tuple[ToolResult, ...] = ()
    state_candidate: str | None = None
    assistant_record: RawRecord | None = None


class TurnTransactionJournal(FrozenModel):
    schema_version: int = 1
    state: TransactionState
    transaction_id: str
    turn_id: str
    checkpoint: PreTurnCheckpoint
    provisional_user_record: RawRecord
    pending_failure_code: str | None = None
    assistant_record: RawRecord | None = None
    target_long_term_document: dict[str, Any] | None = None
    target_long_term_sha256: str | None = None

    @model_validator(mode="after")
    def validate_state_fields(self) -> "TurnTransactionJournal":
        if self.schema_version != 1:
            raise ValueError("轮次事务 Schema 版本不受支持")
        if self.provisional_user_record.role != Role.USER:
            raise ValueError("轮次事务暂存记录必须是 USER")
        if self.provisional_user_record.turn_id != self.turn_id:
            raise ValueError("轮次事务 turn 身份不一致")
        target_values = (self.target_long_term_document, self.target_long_term_sha256)
        if (target_values[0] is None) != (target_values[1] is None):
            raise ValueError("长期记忆目标与摘要必须同时存在")
        if self.state == TransactionState.PREPARED and any(
            value is not None
            for value in (
                self.pending_failure_code,
                self.assistant_record,
                self.target_long_term_document,
                self.target_long_term_sha256,
            )
        ):
            raise ValueError("PREPARED 不得包含待发布结果")
        if self.state == TransactionState.READY_PENDING:
            if not self.pending_failure_code or any(
                value is not None
                for value in (
                    self.assistant_record,
                    self.target_long_term_document,
                    self.target_long_term_sha256,
                )
            ):
                raise ValueError("READY_PENDING 只能发布一条 USER pending")
        if self.state == TransactionState.READY_TO_COMMIT:
            if self.assistant_record is None:
                raise ValueError("READY_TO_COMMIT 必须包含 assistant 记录")
            if (
                self.assistant_record.role != Role.ASSISTANT
                or self.assistant_record.turn_id != self.turn_id
                or self.assistant_record.global_sequence
                != self.provisional_user_record.global_sequence + 1
            ):
                raise ValueError("READY_TO_COMMIT 的 assistant 身份或顺序无效")
        return self


@dataclass(frozen=True, slots=True)
class PersonaProfile:
    persona_id: str
    role: str
    prompt_path: str
    prompt: str
    model_profile_id: str
    allowed_tool_ids: tuple[str, ...] = ()
    allowed_template_variables: tuple[str, ...] = ()
    prompt_sha256: str = ""


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
    assets: tuple[ConfigAsset, ...] = ()

    @classmethod
    def create(cls, **values: Any) -> "ConfigSnapshot":
        values["prompts"] = _freeze(dict(values["prompts"]))
        values["settings"] = _freeze(dict(values["settings"]))
        return cls(**values)
