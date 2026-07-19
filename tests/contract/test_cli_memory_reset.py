"""[2026-07-19] 记忆重置命令使用显式作用域，不增加交互确认或会话概念。"""

from pathlib import Path

from bai_agent.cli import _parser, main


def test_memory_reset_parser_has_two_simple_scopes() -> None:
    reset_all = _parser().parse_args(["memory", "reset", "all"])
    reset_long_term = _parser().parse_args(["memory", "reset", "long-term"])
    assert reset_all.memory_command == "reset"
    assert reset_all.reset_scope == "all"
    assert reset_long_term.reset_scope == "long-term"


def test_memory_reset_all_cli_returns_safe_summary(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "--config-dir",
            "config",
            "--data-dir",
            str(tmp_path / "data"),
            "memory",
            "reset",
            "all",
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"scope":"all"' in output
    assert '"raw_records_after":0' in output
    assert '"long_term_items_after":0' in output
    assert "content" not in output
