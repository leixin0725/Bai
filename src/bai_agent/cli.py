"""[2026-07-19] CLI 只暴露稳定命令和安全 JSON，不输出正文以外的敏感数据。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from bai_agent.config.loader import load_config
from bai_agent.domain.errors import BaiError
from bai_agent.security.incidents import IncidentStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bai-agent")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    config = commands.add_parser("config")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("validate")

    security = commands.add_parser("security")
    security_sub = security.add_subparsers(dest="security_command", required=True)
    incident = security_sub.add_parser("incident")
    incident_sub = incident.add_subparsers(dest="incident_command", required=True)
    incident_sub.add_parser("check")
    acknowledge = incident_sub.add_parser("acknowledge")
    acknowledge.add_argument("--rotation-reference")
    acknowledge.add_argument("--repository-scan-revision")
    acknowledge.add_argument("--runtime-scan-revision")
    acknowledge.add_argument("--disposition-record")
    return parser


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "config" and args.config_command == "validate":
            snapshot = load_config(args.config_dir)
            _print(
                {
                    "ok": True,
                    "config_revision": snapshot.revision,
                    "personas": [item.persona_id for item in snapshot.personas],
                    "states": [snapshot.default_state_id],
                }
            )
            return 0
        if args.command == "security":
            store = IncidentStore(args.data_dir)
            if args.incident_command == "acknowledge":
                store.acknowledge(
                    rotation_reference=args.rotation_reference,
                    repository_scan_revision=args.repository_scan_revision,
                    runtime_scan_revision=args.runtime_scan_revision,
                    disposition_record=args.disposition_record,
                )
            report = store.check()
            _print({"ok": report.cleared, **report.model_dump()})
            return 0 if report.cleared else 7
    except BaiError as exc:
        _print({"ok": False, "error": exc.as_dict()})
        return 3 if exc.code.startswith("CREDENTIAL") else 2
    return 2


if __name__ == "__main__":
    sys.exit(main())

