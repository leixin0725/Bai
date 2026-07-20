"""[2026-07-19] 所有持久化入口复用同一凭据检测与外部秘密读取边界。"""

from __future__ import annotations

from hashlib import sha256
import json
import os
import re
from typing import Mapping

from bai_agent.domain.errors import BaiError, CredentialExposureError


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


class PromptCredentialGuard:
    """[2026-07-20] 显示前和发送前使用同一无原值门禁，认证信息由 transport 单独持有。"""

    def __init__(self, incident_store=None) -> None:
        self.guard = CredentialGuard()
        self.incident_store = incident_store

    def _check(self, payload):
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        fingerprints = self.guard.find_fingerprints(rendered)
        if fingerprints:
            if self.incident_store is not None:
                self.incident_store.open(
                    fingerprint=fingerprints[0],
                    artifacts=["prompt-payload"],
                )
            raise CredentialExposureError()
        return payload

    def before_display(self, payload):
        return self._check(payload)

    def before_send(self, payload):
        return self._check(payload)


def read_secret(variable: str, environ: Mapping[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    secret = values.get(variable)
    if not secret:
        raise BaiError("CREDENTIAL_MISSING", f"所需凭据环境变量 {variable} 不存在。")
    return secret
