"""[2026-07-19] 所有测试默认使用隔离数据根，避免污染用户运行记忆。"""

from pathlib import Path

import pytest

from tests.fakes import DeterministicClock, FakeProvider


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def deterministic_clock() -> DeterministicClock:
    return DeterministicClock()

