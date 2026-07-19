"""[2026-07-19] 配置校验集中维护数值、路径与引用不变量。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bai_agent.domain.errors import BaiError


def resolve_inside(root: Path, reference: str) -> Path:
    candidate = (root / reference).resolve()
    resolved_root = root.resolve()
    if Path(reference).is_absolute() or not candidate.is_relative_to(resolved_root):
        raise BaiError("CONFIG_PATH_ESCAPE", "配置引用越过配置根。")
    if candidate.is_symlink():
        raise BaiError("CONFIG_PATH_ESCAPE", "配置引用不能使用越界符号链接。")
    return candidate


def require_mapping(value: Any, logical_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BaiError("CONFIG_INVALID", f"{logical_name} 必须是 TOML 表。")
    return value


def validate_agent(agent: dict[str, Any]) -> None:
    try:
        archive = require_mapping(agent["archive"], "archive")
        short = require_mapping(agent["short_term"], "short_term")
        budget = require_mapping(agent["context_budget"], "context_budget")
        if int(archive["max_record_bytes"]) > int(archive["segment_max_bytes"]):
            raise ValueError
        if int(short["reserved_records"]) >= int(short["max_records"]):
            raise ValueError
        if int(short["curation_batch_min_records"]) > int(short["curation_batch_max_records"]):
            raise ValueError
        component_total = sum(
            int(budget[name])
            for name in (
                "trusted_instruction_tokens",
                "long_term_tokens",
                "short_term_tokens",
                "tool_result_tokens",
            )
        )
        if component_total > int(budget["max_input_tokens"]):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise BaiError("CONFIG_INVALID", "配置数值缺失、类型错误或交叉约束无效。") from exc


def read_utf8_nonempty(path: Path, max_bytes: int = 262_144) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise BaiError("CONFIG_REFERENCE_MISSING", "配置引用文件不存在或不可读。") from exc
    if not payload or len(payload) > max_bytes:
        raise BaiError("CONFIG_INVALID", "配置引用文件为空或超过大小限制。")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BaiError("CONFIG_INVALID", "配置引用文件必须使用 UTF-8。") from exc
    if not text.strip():
        raise BaiError("CONFIG_INVALID", "配置引用文件不得为空白。")
    return text

