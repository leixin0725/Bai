"""[2026-07-19] 明文记忆权限统一归一化为 private、too_broad 或 unverifiable。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path


class PermissionStatus(StrEnum):
    PRIVATE = "private"
    TOO_BROAD = "too_broad"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True, slots=True)
class PermissionResult:
    status: PermissionStatus
    error_code: str | None = None
    warning: str | None = None


def ensure_private_path(path: Path, *, is_directory: bool) -> PermissionResult:
    """[2026-08-10] 仅支持 POSIX（Ubuntu/WSL）：收紧 0700/0600，拒绝符号链接。"""
    try:
        resolved = path.resolve(strict=True)
        if path.is_symlink() or resolved.is_symlink():
            return PermissionResult(
                PermissionStatus.UNVERIFIABLE,
                "MEMORY_PATH_LINK_REJECTED",
                "明文记忆路径不能使用符号链接。",
            )
        os.chmod(path, 0o700 if is_directory else 0o600)
        actual = path.stat().st_mode & 0o777
        expected = 0o700 if is_directory else 0o600
        if actual != expected:
            return PermissionResult(PermissionStatus.TOO_BROAD, "MEMORY_PERMISSION_TOO_BROAD", "明文记忆权限过宽。")
        return PermissionResult(PermissionStatus.PRIVATE)
    except OSError:
        return PermissionResult(
            PermissionStatus.UNVERIFIABLE,
            "MEMORY_PERMISSION_UNVERIFIABLE",
            "无法确认明文记忆文件权限。",
        )
