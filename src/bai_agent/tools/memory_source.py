"""[2026-07-19] 来源查询只接受 memory_id/游标，并只读取当前 revision 的权威记录。"""

from __future__ import annotations

import base64
import json

from bai_agent.domain.models import (
    SourceKind,
    SourceRef,
    ToolDefinition,
    ToolOutcome,
    ToolResult,
)


class MemorySourceQueryTool:
    name = "memory_source_query"

    def __init__(self, store, archive, *, page_size: int = 16, tracer=None) -> None:
        self.store = store
        self.archive = archive
        self.page_size = page_size
        self.tracer = tracer
        self.definition = ToolDefinition(
            tool_id=self.name,
            name=self.name,
            description="按长期记忆 ID 只读查询有序原始来源。",
            input_schema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "cursor": {"type": "string"},
                },
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            safety={"read_only": True, "destructive": False, "idempotent": True},
        )

    @staticmethod
    def _encode_cursor(revision: int, offset: int) -> str:
        payload = json.dumps({"revision": revision, "offset": offset}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str, revision: int) -> int:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode())
            if payload["revision"] != revision or not isinstance(payload["offset"], int):
                raise ValueError
            return payload["offset"]
        except Exception as exc:
            raise ValueError("cursor invalid") from exc

    def execute_sync(self, arguments: dict, context) -> ToolResult:
        document = self.store.load()
        memory = next(
            (item for item in document.memories if item.memory_id == arguments.get("memory_id")),
            None,
        )
        if memory is None:
            return ToolResult(call_id="host", outcome=ToolOutcome.NOT_FOUND, error_code="MEMORY_NOT_FOUND")
        try:
            offset = self._decode_cursor(arguments["cursor"], document.revision) if arguments.get("cursor") else 0
        except ValueError:
            return ToolResult(call_id="host", outcome=ToolOutcome.INVALID_ARGUMENTS, error_code="CURSOR_INVALID")
        raw = {item.record_id: item for item in self.archive.read_all()}
        refs = sorted(memory.source_refs, key=lambda ref: (raw[ref.record_id].global_sequence, ref.record_id))
        page_refs = refs[offset : offset + self.page_size]
        records = []
        for reference in page_refs:
            record = raw.get(reference.record_id)
            if record is None:
                return ToolResult(call_id="host", outcome=ToolOutcome.EXECUTION_FAILURE, error_code="SOURCE_RECORD_MISSING")
            if record.content_sha256 != reference.record_sha256:
                return ToolResult(call_id="host", outcome=ToolOutcome.EXECUTION_FAILURE, error_code="SOURCE_HASH_MISMATCH")
            records.append(
                {
                    "record_id": record.record_id,
                    "global_sequence": record.global_sequence,
                    "role": record.role.value,
                    "content": self.store.guard.ensure_safe(record.content),
                    "created_at": record.created_at.isoformat(),
                    "state_id": record.state_id,
                    "content_sha256": record.content_sha256,
                }
            )
        next_offset = offset + len(page_refs)
        result = ToolResult(
            call_id="host",
            outcome=ToolOutcome.SUCCESS,
            data={
                "flow_id": context.flow_id,
                "memory_id": memory.memory_id,
                "memory_revision": document.revision,
                "source_count": len(refs),
                "records": records,
                "next_cursor": (
                    self._encode_cursor(document.revision, next_offset)
                    if next_offset < len(refs)
                    else None
                ),
            },
        )
        if self.tracer:
            self.tracer.emit(
                "memory.source_query",
                flow_id=context.flow_id,
                turn_id=context.turn_id,
                persona_id=context.persona_id,
                state_id=context.state_id,
                memory_id=memory.memory_id,
                revision=document.revision,
                source_count=len(records),
                result_code=result.outcome.value,
                cursor_present=bool(arguments.get("cursor")),
            )
        return result

    async def execute(self, arguments: dict, context) -> ToolResult:
        return self.execute_sync(arguments, context)

    @staticmethod
    def result_source(call_id: str) -> SourceRef:
        """[2026-07-20] 工具结果使用运行时 producer 与 call id，不伪造文件来源。"""
        return SourceRef(
            source_kind=SourceKind.RUNTIME,
            source_id=f"tool-result:{call_id}",
            entity_ids=(call_id,),
            producer="tool:memory_source_query",
        )
