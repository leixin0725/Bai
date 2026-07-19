"""[2026-07-19] 所有持久化入口复用同一凭据检测与外部秘密读取边界。"""

from __future__ import annotations

from hashlib import sha256
import os
import re
from typing import Mapping

from bai_agent.domain.errors import BaiError


_CREDENTIALS = (
    re.compile(r"sk-[A-Za-z0-9._-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._-]{16,}"),
)


def secret_fingerprint(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()[:16]


class CredentialGuard:
    def find_fingerprints(self, content: str) -> tuple[str, ...]:
        values: list[str] = []
        for pattern in _CREDENTIALS:
            values.extend(secret_fingerprint(match.group(0)) for match in pattern.finditer(content))
        return tuple(values)

    def ensure_safe(self, content: str) -> str:
        if self.find_fingerprints(content):
            raise BaiError("CREDENTIAL_REJECTED", "内容疑似包含可用凭据，原值未保存。")
        return content


def read_secret(variable: str, environ: Mapping[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    secret = values.get(variable)
    if not secret:
        raise BaiError("CREDENTIAL_MISSING", f"所需凭据环境变量 {variable} 不存在。")
    return secret

