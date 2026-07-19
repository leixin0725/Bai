"""[2026-07-19] 凭据事件在四项处置证据齐全前持续阻塞交付。"""

from pathlib import Path

from bai_agent.security.incidents import IncidentStore
from tests.security_scanner import scan_tree


# [2026-07-19] 受控值只在隔离临时目录运行时组合，仓库文本本身不携带可用形态。
CONTROLLED_SECRET = "AKIA" + "1" * 16


def test_scanner_reports_scope_without_secret_value(tmp_path: Path) -> None:
    areas = ["source.py", "history.txt", "raw.jsonl", "long_term.yaml", "dist.txt", "agent.log"]
    for name in areas:
        (tmp_path / name).write_text(f"controlled={CONTROLLED_SECRET}", encoding="utf-8")
    findings = scan_tree(tmp_path, list(tmp_path.iterdir()), scope="isolated-fixture")
    rendered = repr(findings)
    assert len(findings) == len(areas)
    assert CONTROLLED_SECRET not in rendered
    assert {finding.logical_path for finding in findings} == set(areas)


def test_incident_check_requires_all_disposition_evidence(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    store.open(fingerprint="sha256:controlled", artifacts=["raw:1", "log:1"])
    assert not store.check().cleared

    store.acknowledge(rotation_reference="rotation-1")
    assert not store.check().cleared
    store.acknowledge(repository_scan_revision="commit-1")
    assert not store.check().cleared
    store.acknowledge(runtime_scan_revision="runtime-1")
    assert not store.check().cleared
    store.acknowledge(disposition_record="case-1")
    report = store.check()
    assert report.cleared
    assert CONTROLLED_SECRET not in report.model_dump_json()
