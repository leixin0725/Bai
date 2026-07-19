"""[2026-07-19] 明文记忆权限统一归一化为 private、too_broad 或 unverifiable。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import getpass
import csv
import io
import os
from pathlib import Path
import subprocess


class PermissionStatus(StrEnum):
    PRIVATE = "private"
    TOO_BROAD = "too_broad"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True, slots=True)
class PermissionResult:
    status: PermissionStatus
    error_code: str | None = None
    warning: str | None = None


def classify_windows_acl(
    aces: list[tuple[str, str, str]], *, query_ok: bool, local_path: bool
) -> PermissionResult:
    if not query_ok or not local_path:
        return PermissionResult(
            PermissionStatus.UNVERIFIABLE,
            "MEMORY_PERMISSION_UNVERIFIABLE",
            "无法确认明文记忆的 Windows 访问控制。",
        )
    allowed = {"current_user", "system", "administrators", "builtin\\administrators", "nt authority\\system"}
    for principal, ace_type, rights in aces:
        normalized = principal.casefold()
        if (
            ace_type.casefold() == "allow"
            and rights.casefold() != "none"
            and normalized not in allowed
            and not normalized.startswith("s-1-5-21-")
        ):
            return PermissionResult(
                PermissionStatus.TOO_BROAD,
                "MEMORY_PERMISSION_TOO_BROAD",
                "明文记忆允许宽泛主体读取或写入。",
            )
    return PermissionResult(PermissionStatus.PRIVATE)


def _windows_local(path: Path) -> bool:
    return not str(path.resolve()).startswith("\\\\")


def _tighten_windows(path: Path, is_directory: bool) -> None:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    whoami = str(Path(system_root) / "System32" / "whoami.exe")
    identity = subprocess.run(
        [whoami, "/user", "/fo", "csv", "/nh"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    try:
        row = next(csv.reader(io.StringIO(identity.stdout)))
        user = f"*{row[1]}" if identity.returncode == 0 and len(row) >= 2 else getpass.getuser()
    except (csv.Error, StopIteration):
        user = getpass.getuser()
    rights = "(OI)(CI)F" if is_directory else "F"
    # [2026-07-19] 先建立当前用户显式 ACE 再移除继承，避免失败中途锁死新目录。
    principals = (user, "*S-1-5-18", "*S-1-5-32-544")
    for principal in principals:
        subprocess.run(
            ["icacls", str(path), "/grant:r", f"{principal}:{rights}"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    subprocess.run(
        ["icacls", str(path), "/inheritance:r"],
        check=False,
        capture_output=True,
        timeout=10,
    )


def _query_windows(path: Path) -> PermissionResult:
    try:
        result = subprocess.run(
            ["icacls", str(path)], check=False, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return classify_windows_acl([], query_ok=False, local_path=_windows_local(path))
    aces: list[tuple[str, str, str]] = []
    for line in result.stdout.splitlines()[1:]:
        lowered = line.casefold()
        for principal in ("Everyone", "Authenticated Users", "Users"):
            if principal.casefold() in lowered and ":(" in line:
                aces.append((principal, "allow", "read_write"))
    return classify_windows_acl(aces, query_ok=result.returncode == 0, local_path=_windows_local(path))


def ensure_private_path(path: Path, *, is_directory: bool) -> PermissionResult:
    try:
        resolved = path.resolve(strict=True)
        is_junction = getattr(path, "is_junction", lambda: False)()
        if path.is_symlink() or resolved.is_symlink() or is_junction:
            return PermissionResult(
                PermissionStatus.UNVERIFIABLE,
                "MEMORY_PATH_LINK_REJECTED",
                "明文记忆路径不能使用符号链接或 junction。",
            )
        if os.name == "nt":
            # [2026-07-19] 已满足私有 DACL 时不重写 ACL，既保留显式授权也缩短只读启动路径。
            current = _query_windows(path)
            if current.status == PermissionStatus.PRIVATE:
                return current
            _tighten_windows(path, is_directory)
            return _query_windows(path)
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
