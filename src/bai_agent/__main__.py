"""[2026-07-19] 模块入口只负责把进程控制交给 CLI。"""

from bai_agent.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

