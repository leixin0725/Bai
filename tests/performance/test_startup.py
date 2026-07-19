"""[2026-07-19] Windows 参考验收测量全新解释器到首轮上下文可用的端到端耗时。"""

from __future__ import annotations

from math import ceil
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import pytest

from tests.fixtures.performance import (
    LONG_TERM_MEMORY_COUNT,
    RAW_RECORD_COUNT,
    RECENT_RECORD_COUNT,
    prepare_performance_dataset,
)


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tests" / "performance" / "baselines" / "windows-reference.json"


def nearest_rank_p95(samples: list[float]) -> float:
    if not samples:
        raise ValueError("性能样本不能为空")
    return sorted(samples)[ceil(0.95 * len(samples)) - 1]


@pytest.mark.performance
@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("BAI_RUN_WINDOWS_REFERENCE") != "1",
    reason="仅在显式启用的 Windows 参考环境运行 3 秒门槛",
)
def test_windows_reference_fresh_process_startup(tmp_path: Path) -> None:
    dataset = prepare_performance_dataset(tmp_path / "performance-data")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    launches = max(100, int(os.environ.get("BAI_PERFORMANCE_LAUNCHES", "100")))
    script = (
        "import json,sys;"
        "from pathlib import Path;"
        "from tests.fixtures.performance import startup_probe;"
        "print(json.dumps(startup_probe(Path(sys.argv[1]),Path(sys.argv[2]))))"
    )
    samples: list[float] = []
    last_result: dict[str, int] = {}
    for _ in range(launches):
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, "-c", script, str(ROOT / "config"), str(dataset.data_dir)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=float(baseline["timeout_seconds"]),
        )
        samples.append(time.perf_counter() - started)
        last_result = json.loads(completed.stdout)

    p95 = nearest_rank_p95(samples)
    report = {
        "os": platform.platform(),
        "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
        "memory": os.environ.get("BAI_REFERENCE_MEMORY", "record-at-run-time"),
        "storage": os.environ.get("BAI_REFERENCE_STORAGE", "record-at-run-time"),
        "python": platform.python_version(),
        "cache_policy": baseline["cache_policy"],
        "launches": launches,
        "nearest_rank_p95_seconds": p95,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    assert last_result["raw_records"] == RAW_RECORD_COUNT
    assert last_result["raw_index"] == RAW_RECORD_COUNT
    assert last_result["long_term_memories"] == LONG_TERM_MEMORY_COUNT
    assert last_result["recent_records"] == RECENT_RECORD_COUNT
    assert last_result["coverage_spans"] > 0
    assert last_result["prompt_segments"] >= 6
    assert last_result["network_calls"] == 0
    assert p95 <= float(baseline["p95_limit_seconds"])
