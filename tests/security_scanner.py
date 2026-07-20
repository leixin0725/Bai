"""[2026-07-19] 测试侧秘密扫描器只返回逻辑位置和不可逆指纹，绝不回显候选值。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import subprocess


SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9._-]{20,}"),
    re.compile(rb"AKIA[A-Z0-9]{16}"),
    re.compile(rb"Bearer\s+[A-Za-z0-9._-]{16,}", re.IGNORECASE),
)
INVALID_MARKERS = (b"test-only", b"placeholder-invalid", b"EXAMPLE", b"example")


@dataclass(frozen=True)
class Finding:
    logical_path: str
    fingerprint: str
    scope: str


def _scan_bytes(data: bytes, logical_path: str, scope: str) -> list[Finding]:
    findings: list[Finding] = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(data):
            candidate = match.group(0)
            if any(marker in candidate for marker in INVALID_MARKERS):
                continue
            findings.append(
                Finding(logical_path, "sha256:" + sha256(candidate).hexdigest()[:16], scope)
            )
    return findings


def scan_tree(root: Path, paths: list[Path], scope: str = "working-tree") -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        logical = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        findings.extend(_scan_bytes(data, logical, scope))
    return findings


def tracked_paths(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [root / line for line in result.stdout.splitlines() if line]


def scan_reachable_history(root: Path) -> list[Finding]:
    """[2026-07-19] 逐提交读取对象内容；报告只保存路径与指纹。"""
    commits = subprocess.run(
        ["git", "rev-list", "--all"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    findings: list[Finding] = []
    for commit in commits:
        names = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", commit],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.splitlines()
        for name in names:
            blob = subprocess.run(
                ["git", "show", f"{commit}:{name}"], cwd=root, capture_output=True
            )
            if blob.returncode == 0:
                findings.extend(_scan_bytes(blob.stdout, name, f"commit:{commit[:12]}"))
    return findings


def scan_git_diffs(root: Path) -> list[Finding]:
    """[2026-07-20] 工作树与暂存补丁单独扫描，覆盖尚未形成文件或提交的候选值。"""
    findings: list[Finding] = []
    for cached, scope in ((False, "working-diff"), (True, "staged-diff")):
        command = ["git", "diff", "--binary", "--no-ext-diff"]
        if cached:
            command.append("--cached")
        result = subprocess.run(command, cwd=root, check=True, capture_output=True)
        findings.extend(_scan_bytes(result.stdout, "git-diff", scope))
    return findings


def persistent_prompt_trace_paths(root: Path) -> list[str]:
    """[2026-07-20] 只检查运行数据位置，不把源码测试名误报成持久 prompt trace。"""
    data_root = root / "data"
    if not data_root.exists():
        return []
    suspicious: list[str] = []
    for path in data_root.rglob("*"):
        if not path.is_file():
            continue
        logical = path.relative_to(root).as_posix()
        lowered = logical.casefold()
        if "prompt-trace" in lowered or "prompt_trace" in lowered:
            suspicious.append(logical)
    return sorted(suspicious)


def temporal_security_surface_paths(root: Path) -> list[Path]:
    """[2026-07-20] 时间配置、来源桥、调试与夹具是本功能的显式秘密扫描面。"""
    candidates = [
        root / "config" / "history_timestamps.toml",
        root / "src" / "bai_agent" / "prompting" / "temporal.py",
        root / "src" / "bai_agent" / "memory" / "temporal.py",
        root / "src" / "bai_agent" / "runtime" / "controller.py",
        root / "src" / "bai_agent" / "debug" / "tui.py",
    ]
    candidates.extend((root / "tests").rglob("*temporal*"))
    return [path for path in candidates if path.is_file()]
