"""[2026-07-19] 仓库、历史、差异和已存在制品必须没有可用凭据。"""

from pathlib import Path

from tests.security_scanner import (
    persistent_prompt_trace_paths,
    scan_git_diffs,
    scan_reachable_history,
    scan_tree,
    tracked_paths,
)


ROOT = Path(__file__).resolve().parents[2]


def test_repository_and_reachable_history_have_no_usable_credentials() -> None:
    paths = tracked_paths(ROOT)
    for optional in (ROOT / "build", ROOT / "dist", ROOT / "data"):
        if optional.exists():
            paths.extend(optional.rglob("*"))
    findings = scan_tree(ROOT, paths) + scan_git_diffs(ROOT) + scan_reachable_history(ROOT)
    assert findings == [], [(item.logical_path, item.fingerprint, item.scope) for item in findings]
    assert persistent_prompt_trace_paths(ROOT) == []
