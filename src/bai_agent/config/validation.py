"""[2026-07-19] 配置校验集中维护数值、路径与引用不变量。"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bai_agent.domain.errors import BaiError


TOKEN_ESTIMATORS = frozenset({"deepseek_character_v1"})

HISTORY_TIMESTAMP_FIELDS = frozenset(
    {
        "schema_version",
        "display_timezone",
        "long_gap_minutes",
        "continuous_segment_refresh_minutes",
        "split_on_local_date_change",
    }
)


def validate_history_timestamps(value: Any) -> dict[str, Any]:
    """[2026-07-20] 独立时间策略严格拒绝缺失、未知、宽松类型和本地时区回退。"""
    try:
        raw = require_mapping(value, "history_timestamps.toml")
    except (TypeError, ValueError) as exc:
        raise BaiError("CONFIG_INVALID", "history_timestamps.toml 根对象类型无效。") from exc
    missing = sorted(HISTORY_TIMESTAMP_FIELDS - set(raw))
    unknown = sorted(set(raw) - HISTORY_TIMESTAMP_FIELDS)
    if missing or unknown:
        detail = "、".join((*[f"缺少 {item}" for item in missing], *[f"未知 {item}" for item in unknown]))
        raise BaiError("CONFIG_INVALID", f"history_timestamps.toml 字段集合无效：{detail}。")
    schema = raw["schema_version"]
    zone_name = raw["display_timezone"]
    gap = raw["long_gap_minutes"]
    refresh = raw["continuous_segment_refresh_minutes"]
    split = raw["split_on_local_date_change"]
    if type(schema) is not int or schema != 1:
        raise BaiError("CONFIG_INVALID", "history_timestamps.toml 字段 schema_version 必须是整数 1。")
    if not isinstance(zone_name, str) or not zone_name.strip():
        raise BaiError("CONFIG_INVALID", "history_timestamps.toml 字段 display_timezone 必须是非空 IANA 名称。")
    if type(gap) is not int or not 1 <= gap <= 1440:
        raise BaiError("CONFIG_INVALID", "history_timestamps.toml 字段 long_gap_minutes 必须是 1..1440 的整数。")
    if type(refresh) is not int or not 1 <= refresh <= 10080:
        raise BaiError("CONFIG_INVALID", "history_timestamps.toml 字段 continuous_segment_refresh_minutes 必须是 1..10080 的整数。")
    if refresh < gap:
        raise BaiError("CONFIG_INVALID", "history_timestamps.toml 字段 continuous_segment_refresh_minutes 必须大于等于 long_gap_minutes。")
    if type(split) is not bool:
        raise BaiError("CONFIG_INVALID", "history_timestamps.toml 字段 split_on_local_date_change 必须是布尔值。")
    try:
        ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise BaiError("CONFIG_INVALID", "history_timestamps.toml 字段 display_timezone 不是可解析的 IANA 时区。") from exc
    return dict(raw)


def validate_debug_prompt(value: Any) -> dict[str, Any]:
    """[2026-07-20] 调试显示策略可缺省，但不能持久化启用状态或绕过 TTY。"""
    raw = {} if value is None else require_mapping(value, "debug_prompt")
    defaults = {
        "color": "auto",
        "high_context_percent": 80,
        "critical_context_percent": 95,
        "estimate_safety_margin_percent": 10,
    }
    result = defaults | raw
    try:
        high = result["high_context_percent"]
        critical = result["critical_context_percent"]
        margin = result["estimate_safety_margin_percent"]
        if result["color"] not in {"auto", "always", "never"}:
            raise ValueError
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (high, critical, margin)):
            raise ValueError
        if not (0 < high < critical <= 100 and 0 <= margin <= 50):
            raise ValueError
        if "enabled" in raw or "debug_prompts" in raw:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise BaiError("CONFIG_INVALID", "debug_prompt 配置字段、类型或阈值无效。") from exc
    return result


def validate_provider_capabilities(provider: dict[str, Any], profile: dict[str, Any]) -> None:
    """[2026-07-20] 模型容量只来自当前 profile；非法输出上限不得静默截断。"""
    try:
        cap = provider["max_output_cap"]
        estimator = provider["token_estimator"]
        output = profile["max_output_tokens"]
        capacity = profile.get("context_window_tokens")
        if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in (cap, output)):
            raise ValueError
        if output > cap:
            raise ValueError
        if capacity is not None:
            if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0 or output > capacity:
                raise ValueError
        if estimator not in TOKEN_ESTIMATORS:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise BaiError("PROVIDER_CAPABILITY_INVALID", "Provider 容量、输出上限或 estimator 配置无效。") from exc


def resolve_inside(root: Path, reference: str) -> Path:
    unresolved = root / reference
    if unresolved.is_symlink():
        raise BaiError("CONFIG_PATH_ESCAPE", "配置引用不能使用符号链接。")
    candidate = unresolved.resolve()
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
        validate_debug_prompt(agent.get("debug_prompt"))
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


def validate_template(
    template: str,
    *,
    allowed_variables: tuple[str, ...],
    untrusted_variables: tuple[str, ...],
) -> None:
    """[2026-07-19] Template 标识符必须与配置清单完全一致，禁止安全降级替换。"""
    found: list[str] = []
    for match in Template.pattern.finditer(template):
        if match.group("invalid") is not None:
            raise BaiError("PROMPT_TEMPLATE_INVALID", "提示模板包含畸形变量。")
        name = match.group("named") or match.group("braced")
        if name:
            found.append(name)
    if set(found) != set(allowed_variables) or len(found) != len(set(found)):
        raise BaiError("PROMPT_TEMPLATE_INVALID", "提示模板变量与允许清单不完全匹配。")
    if not set(untrusted_variables).issubset(set(allowed_variables)):
        raise BaiError("PROMPT_TEMPLATE_INVALID", "不可信数据变量未包含在允许清单中。")
    trusted_only = {"trusted_personas", "curator_persona", "untrusted_boundary", "output_schema"}
    if trusted_only & set(untrusted_variables):
        raise BaiError("PROMPT_TEMPLATE_INVALID", "可信指令变量不能标记为不可信数据。")
    try:
        Template(template).substitute({name: "fixture" for name in allowed_variables})
    except (KeyError, ValueError) as exc:
        raise BaiError("PROMPT_TEMPLATE_INVALID", "提示模板无法严格替换。") from exc
