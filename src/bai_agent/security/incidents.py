"""[2026-07-19] 凭据事件处置状态只保存指纹和逻辑制品 ID，缺项即阻塞。"""

from __future__ import annotations

from pathlib import Path
import json
import os
import tempfile

from pydantic import BaseModel, ConfigDict, Field


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
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
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
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def open(self, *, fingerprint: str, artifacts: list[str]) -> None:
        self._write({"open": True, "fingerprint": fingerprint, "artifacts": sorted(set(artifacts))})

    def acknowledge(self, **evidence: str | None) -> None:
        payload = self._load()
        for name, value in evidence.items():
            if value:
                payload[name] = value
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

