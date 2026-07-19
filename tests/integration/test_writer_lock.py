"""[2026-07-19] 跨进程只能有一个原始记忆写入者。"""

from pathlib import Path
import subprocess
import sys
import time


def test_two_processes_compete_for_single_writer_lock(tmp_path: Path) -> None:
    lock = tmp_path / "memory" / ".state" / "writer.lock"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    script = (
        "from pathlib import Path; import sys,time; from bai_agent.memory.recovery import WriterLease; "
        "lease=WriterLease(Path(sys.argv[1])); lease.acquire(); Path(sys.argv[2]).write_text('ready'); "
        "deadline=time.time()+10; "
        "exec(\"while not Path(sys.argv[3]).exists() and time.time()<deadline:\\n time.sleep(.02)\"); lease.release()"
    )
    first = subprocess.Popen([sys.executable, "-c", script, str(lock), str(ready), str(release)])
    for _ in range(100):
        if ready.exists():
            break
        first.poll()
        time.sleep(0.02)
    assert ready.exists()
    contender = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from bai_agent.memory.recovery import WriterLease; WriterLease(Path(r'" + str(lock) + "')).acquire()",
        ],
        capture_output=True,
        timeout=10,
    )
    assert contender.returncode != 0
    release.write_text("release", encoding="utf-8")
    assert first.wait(timeout=10) == 0
