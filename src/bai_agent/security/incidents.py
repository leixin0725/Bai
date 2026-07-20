"""[2026-07-20] 凭据事件状态只接受脱敏逻辑标识；未知字段或正文注入统一失败关闭。"""

from __future__ import annotations

from pathlib import Path
import json
import os
import re
import tempfile

from pydantic import BaseModel, ConfigDict, Field

from bai_agent.domain.errors import BaiError
from bai_agent.security.permissions import PermissionStatus, ensure_private_path


_SAFE_METADATA = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")
_PERSISTED_FIELDS = frozenset(
    {
        "open",
        "fingerprint",
        "artifacts",
        "rotation_reference",
        "repository_scan_revision",
        "runtime_scan_revision",
        "disposition_record",
    }
)


def _require_safe_metadata(value: str, field: str) -> str:
    if not _SAFE_METADATA.fullmatch(value):
        raise BaiError("SECURITY_METADATA_INVALID", f"安全事件字段 {field} 不是脱敏逻辑标识。")
    return value


class IncidentReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    open: bool = False
    fingerprint: str | None = None
    artifacts: tuple[str, ...] = ()
    rotation_reference: str | None = None
    repository_scan_revision: str | None = None
    runtime_scan_revision: str | None = None
    disposition_record: str | None = None
    cleared: bool = True
    missing: tuple[str, ...] = ()


class IncidentStore:
    def __init__(self, data_root: Path) -> None:
        self.path = data_root / ".state" / "security-incident.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"open": False}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) - _PERSISTED_FIELDS:
                raise ValueError
            if not isinstance(payload.get("open"), bool):
                raise ValueError
            for name, value in payload.items():
                if name in {"open", "artifacts"} or value is None:
                    continue
                _require_safe_metadata(value, name)
            artifacts = payload.get("artifacts", [])
            if not isinstance(artifacts, list) or not all(isinstance(item, str) for item in artifacts):
                raise ValueError
            payload["artifacts"] = [
                _require_safe_metadata(item, "artifacts") for item in artifacts
            ]
            return payload
        except (OSError, json.JSONDecodeError, TypeError, ValueError, BaiError):
            return {"open": True, "fingerprint": "sha256:unreadable", "artifacts": ["incident-state"]}

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix="incident-", suffix=".tmp-atomic", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
            permissions = (
                ensure_private_path(self.path.parent, is_directory=True),
                ensure_private_path(self.path, is_directory=False),
            )
            if any(item.status != PermissionStatus.PRIVATE for item in permissions):
                raise BaiError("SECURITY_STATE_PERMISSION_INVALID", "安全事件状态权限无法确认为私有。")
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def open(self, *, fingerprint: str, artifacts: list[str]) -> None:
        self._write(
            {
                "open": True,
                "fingerprint": _require_safe_metadata(fingerprint, "fingerprint"),
                "artifacts": sorted(
                    {_require_safe_metadata(item, "artifacts") for item in artifacts}
                ),
            }
        )

    def acknowledge(self, **evidence: str | None) -> None:
        payload = self._load()
        for name, value in evidence.items():
            if value:
                payload[name] = _require_safe_metadata(value, name)
        self._write(payload)

    def check(self) -> IncidentReport:
        payload = self._load()
        if not payload.get("open"):
            return IncidentReport(open=False, cleared=True)
        required = (
            "rotation_reference",
            "repository_scan_revision",
            "runtime_scan_revision",
            "disposition_record",
        )
        missing = tuple(name for name in required if not payload.get(name))
        return IncidentReport(**payload, cleared=not missing, missing=missing)

    def require_clear(self) -> None:
        """[2026-07-20] 未闭环安全事件在 prompt 展示和发送前共用同一阻断门禁。"""
        from bai_agent.domain.errors import BaiError

        report = self.check()
        if not report.cleared:
            raise BaiError("SECURITY_INCIDENT_OPEN", "安全事件尚未闭环，模型调用已阻止。")
