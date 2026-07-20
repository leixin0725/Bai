"""[2026-07-20] 来源完整性只按构建期 pointer/span 证明，不对正文做反向猜测。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bai_agent.domain.errors import TraceIntegrityError
from bai_agent.domain.models import Participation, RequestPart, canonical_json, content_hash, thaw_json


def canonical_payload_sha256(payload: Any) -> str:
    return content_hash(canonical_json(thaw_json(payload)))


def resolve_json_pointer(payload: Any, pointer: str) -> Any:
    if pointer == "":
        return payload
    if not pointer.startswith("/"):
        raise TraceIntegrityError("JSON Pointer 格式无效，调用已阻止。")
    current = payload
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, Mapping):
                current = current[token]
            elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
                current = current[int(token)]
            else:
                raise KeyError(token)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise TraceIntegrityError("JSON Pointer 无法回读最终载荷，调用已阻止。") from exc
    return current


def validate_provenance(payload: Any, parts: tuple[RequestPart, ...]) -> None:
    included = [item for item in parts if item.participation == Participation.INCLUDED]
    if not included:
        raise TraceIntegrityError("最终请求没有可归因片段，调用已阻止。")
    orders = [item.order for item in included]
    if orders != sorted(orders) or len(orders) != len(set(orders)):
        raise TraceIntegrityError("提示片段顺序重复或乱序，调用已阻止。")
    for part in included:
        if not part.sources or part.payload_pointer is None:
            raise TraceIntegrityError()
        target = resolve_json_pointer(payload, part.payload_pointer)
        if part.text_span is None:
            expected = canonical_json(thaw_json(target)) if not isinstance(target, str) else target
            if expected != part.content:
                raise TraceIntegrityError("提示片段与最终载荷不一致，调用已阻止。")
            continue
        if not isinstance(target, str):
            raise TraceIntegrityError("正文区间只能用于字符串字段，调用已阻止。")
        start, end = part.text_span
        if start < 0 or end < start or end > len(target) or target[start:end] != part.content:
            raise TraceIntegrityError("提示正文区间与最终载荷不一致，调用已阻止。")
    if any(item.participation == Participation.UNKNOWN_SOURCE for item in parts):
        raise TraceIntegrityError()
