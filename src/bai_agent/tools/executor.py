"""[2026-07-19] 工具执行器在调用前完成参数 Schema 和宿主身份授权。"""

from __future__ import annotations

import asyncio
import json

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import ToolCall, ToolExecutionContext, ToolOutcome, ToolResult


def validate_object_schema(arguments: dict, schema: dict) -> None:
    required = set(schema.get("required", []))
    properties = schema.get("properties", {})
    if required - set(arguments):
        raise BaiError("TOOL_ARGUMENTS_INVALID", "工具缺少必需参数。")
    if schema.get("additionalProperties") is False and set(arguments) - set(properties):
        raise BaiError("TOOL_ARGUMENTS_INVALID", "工具包含未声明参数。")
    for name, value in arguments.items():
        expected = properties.get(name, {}).get("type")
        if expected == "string" and not isinstance(value, str):
            raise BaiError("TOOL_ARGUMENTS_INVALID", "工具参数类型无效。")


class ToolExecutor:
    def __init__(self, registry, *, deadline_seconds: float = 20, max_result_bytes: int = 131072) -> None:
        self.registry = registry
        self.deadline_seconds = deadline_seconds
        self.max_result_bytes = max_result_bytes

    async def execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolResult:
        try:
            registered = self.registry.resolve(call.name, context.persona_id)
            validate_object_schema(call.arguments, registered.definition.input_schema)
            result = await asyncio.wait_for(
                registered.implementation.execute(call.arguments, context),
                timeout=self.deadline_seconds,
            )
            if len(json.dumps(result.data, ensure_ascii=False).encode("utf-8")) > self.max_result_bytes:
                return ToolResult(call_id=call.call_id, outcome=ToolOutcome.EXECUTION_FAILURE, error_code="TOOL_RESULT_TOO_LARGE")
            return result.model_copy(update={"call_id": call.call_id})
        except asyncio.TimeoutError:
            return ToolResult(call_id=call.call_id, outcome=ToolOutcome.TIMEOUT, error_code="TOOL_TIMEOUT")
        except BaiError as exc:
            outcome = ToolOutcome.DENIED if exc.code in {"TOOL_DENIED", "TOOL_DISABLED"} else ToolOutcome.INVALID_ARGUMENTS
            return ToolResult(call_id=call.call_id, outcome=outcome, error_code=exc.code)
        except Exception:
            return ToolResult(call_id=call.call_id, outcome=ToolOutcome.EXECUTION_FAILURE, error_code="TOOL_EXECUTION_FAILED")

