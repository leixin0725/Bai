"""[2026-07-19] 工具执行器在调用前完成参数 Schema 和宿主身份授权。"""

from __future__ import annotations

import asyncio
import inspect
import json

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import ToolCall, ToolExecutionContext, ToolOutcome, ToolResult
from bai_agent.security.credentials import CredentialGuard


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
    def __init__(
        self,
        registry,
        *,
        deadline_seconds: float = 20,
        max_result_bytes: int = 131072,
        tracer=None,
    ) -> None:
        self.registry = registry
        self.deadline_seconds = deadline_seconds
        self.max_result_bytes = max_result_bytes
        self.tracer = tracer
        self.guard = CredentialGuard()
        self._serial_lock = asyncio.Lock()

    async def execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolResult:
        # [2026-07-19] 首版串行执行工具，避免共享状态工具产生竞态或乱序审计。
        async with self._serial_lock:
            result = await self._execute(call, context)
        if self.tracer:
            self.tracer.emit(
                "tool.executed",
                call_id=call.call_id,
                flow_id=context.flow_id,
                turn_id=context.turn_id,
                persona_id=context.persona_id,
                state_id=context.state_id,
                trigger_record_id=context.trigger_record_id,
                tool_id=call.name,
                result_code=result.outcome.value,
            )
        return result

    async def _execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolResult:
        registered = None
        prepared = None
        write_started = False

        async def recover_write() -> str | None:
            if registered is None or registered.read_only or not write_started:
                return None
            try:
                if callable(getattr(registered.implementation, "rollback", None)):
                    recovered = registered.implementation.rollback(prepared)
                else:
                    recovered = registered.implementation.compensate(call.arguments, context)
                if inspect.isawaitable(recovered):
                    await recovered
                return None
            except Exception:
                return "TOOL_ROLLBACK_FAILED"

        try:
            self.guard.ensure_safe(json.dumps(call.arguments, ensure_ascii=False))
            registered = self.registry.resolve(call.name, context.persona_id)
            validate_object_schema(call.arguments, registered.definition.input_schema)
            if not registered.read_only:
                try:
                    if callable(getattr(registered.implementation, "prepare", None)):
                        prepared = registered.implementation.prepare(call.arguments, context)
                        if inspect.isawaitable(prepared):
                            prepared = await prepared
                    write_started = True
                except Exception as exc:
                    raise BaiError("TOOL_PREPARE_FAILED", "写工具准备失败，未执行任何副作用。") from exc
            result = await asyncio.wait_for(
                registered.implementation.execute(call.arguments, context),
                timeout=self.deadline_seconds,
            )
            serialized = json.dumps(result.data, ensure_ascii=False)
            self.guard.ensure_safe(serialized)
            if len(serialized.encode("utf-8")) > self.max_result_bytes:
                recovery_error = await recover_write()
                return ToolResult(
                    call_id=call.call_id, outcome=ToolOutcome.EXECUTION_FAILURE,
                    error_code=recovery_error or "TOOL_RESULT_TOO_LARGE",
                )
            if result.outcome != ToolOutcome.SUCCESS:
                recovery_error = await recover_write()
                return result.model_copy(
                    update={
                        "call_id": call.call_id,
                        **({"error_code": recovery_error} if recovery_error else {}),
                    }
                )
            if not registered.read_only and callable(getattr(registered.implementation, "commit", None)):
                committed = registered.implementation.commit(prepared)
                if inspect.isawaitable(committed):
                    await committed
                write_started = False
            return result.model_copy(update={"call_id": call.call_id})
        except asyncio.CancelledError:
            await asyncio.shield(recover_write())
            raise
        except asyncio.TimeoutError:
            recovery_error = await recover_write()
            return ToolResult(
                call_id=call.call_id, outcome=ToolOutcome.TIMEOUT,
                error_code=recovery_error or "TOOL_TIMEOUT",
            )
        except BaiError as exc:
            recovery_error = await recover_write()
            if exc.code == "TOOL_NOT_FOUND":
                outcome = ToolOutcome.NOT_FOUND
            elif exc.code in {"TOOL_DENIED", "TOOL_DISABLED"}:
                outcome = ToolOutcome.DENIED
            else:
                outcome = ToolOutcome.INVALID_ARGUMENTS
            return ToolResult(
                call_id=call.call_id, outcome=outcome,
                error_code=recovery_error or exc.code,
            )
        except Exception:
            recovery_error = await recover_write()
            return ToolResult(
                call_id=call.call_id, outcome=ToolOutcome.EXECUTION_FAILURE,
                error_code=recovery_error or "TOOL_EXECUTION_FAILED",
            )
