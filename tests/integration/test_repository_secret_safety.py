"""[2026-07-19] 仓库、历史、差异和已存在制品必须没有可用凭据。"""

from pathlib import Path

from tests.security_scanner import (
    persistent_prompt_trace_paths,
    scan_git_diffs,
    scan_reachable_history,
    scan_tree,
    temporal_security_surface_paths,
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


def test_timestamp_config_marker_debug_and_fixture_surfaces_add_no_secret_or_trace() -> None:
    surfaces = temporal_security_surface_paths(ROOT)
    assert ROOT / "config" / "history_timestamps.toml" in surfaces
    assert scan_tree(ROOT, surfaces, scope="temporal-surfaces") == []
    assert persistent_prompt_trace_paths(ROOT) == []
    config = (ROOT / "config" / "history_timestamps.toml").read_text(encoding="utf-8")
    assert "api_key" not in config.casefold()
    assert "authorization" not in config.casefold()
