"""[2026-07-19] CLI 只暴露稳定命令和安全 JSON，不输出正文以外的敏感数据。"""

from __future__ import annotations

import argparse
import asyncio
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

    chat = commands.add_parser("chat")
    chat.add_argument("--resume-pending", action="store_true")

    memory = commands.add_parser("memory")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    memory_sub.add_parser("validate")
    commands.add_parser("doctor")

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
        if args.command == "memory" and args.memory_command == "validate":
            from bai_agent.memory.archive import RawRecordArchive

            snapshot = load_config(args.config_dir, require_credentials=False)
            archive = RawRecordArchive(args.data_dir / "memory")
            records = archive.read_all()
            archive.validate_permissions()
            _print({"ok": True, "raw_records": len(records), "config_revision": snapshot.revision})
            return 0
        if args.command == "doctor":
            from bai_agent.memory.archive import RawRecordArchive

            snapshot = load_config(args.config_dir, require_credentials=False)
            archive = RawRecordArchive(args.data_dir / "memory")
            archive.read_all()
            archive.validate_permissions()
            _print(
                {
                    "ok": True,
                    "config_revision": snapshot.revision,
                    "state": snapshot.default_state_id,
                    "network_probe": False,
                }
            )
            return 0
        if args.command == "chat":
            from bai_agent.application import build_application

            app = build_application(args.config_dir, args.data_dir, on_output=print)
            try:
                pending = app.archive.pending_turn()
                if pending and not args.resume_pending:
                    _print({"ok": False, "pending_turn_id": pending.turn_id, "resume_required": True})
                    return 5
                if pending and args.resume_pending:
                    asyncio.run(app.run_turn(pending.content, resume_pending=True, turn_id=pending.turn_id))
                for line in sys.stdin:
                    content = line.rstrip("\r\n")
                    if content:
                        asyncio.run(app.run_turn(content))
                return 0
            finally:
                app.close()
    except BaiError as exc:
        _print({"ok": False, "error": exc.as_dict()})
        if exc.code == "WRITER_LOCKED":
            return 4
        if exc.code.startswith("RAW_") or exc.code.startswith("MEMORY_"):
            return 5
        if exc.code.startswith("PROVIDER_"):
            return 6
        return 3 if exc.code.startswith("CREDENTIAL") else 2
    except KeyboardInterrupt:
        return 130
    return 2


if __name__ == "__main__":
    sys.exit(main())
