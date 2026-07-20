"""[2026-07-19] 脱敏层以不可逆占位符替换候选凭据，不保留原值片段。"""

from __future__ import annotations

import re


_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9._-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._-]{16,}"),
)


def redact_text(value: str) -> str:
    result = value
    for pattern in _PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def safe_prompt_error(code: str) -> str:
    """[2026-07-20] prompt 安全错误只返回稳定错误码，不拼接载荷或匹配片段。"""
    return f"{code}: 提示载荷安全检查失败；原值未显示或记录。"
