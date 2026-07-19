"""[2026-07-19] 长期记忆、来源、概览和前沿在任一故障点共同成功或共同失败。"""

from pathlib import Path

import pytest

from bai_agent.domain.models import CoverageSpan, CurationCheckpoint, MemoryCoverageOverview, Role
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.long_term import LongTermStore
from tests.fakes import FailureInjector


REVISION = "sha256:" + "1" * 64


def prepared(tmp_path: Path, point=None):
    archive = RawRecordArchive(tmp_path)
    record = archive.append(
        role=Role.USER,
        content="原始事实",
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default",
        config_revision=REVISION,
    )
    store = LongTermStore(tmp_path, archive)
    store.initialize()
    document = store.load()
    span = CoverageSpan(
        start_sequence=1,
        end_sequence=1,
        batch_id="batch-00000000-0000-4000-8000-000000000001",
        record_ids=(record.record_id,),
        record_hashes=(record.content_sha256,),
    )
    updated = document.model_copy(
        update={
            "revision": 1,
            "curation": CurationCheckpoint(
                curated_through_sequence=1,
                last_batch_id=span.batch_id,
                updated_at=record.created_at,
                covered_record_ids=(record.record_id,),
            ),
            "coverage_overview": MemoryCoverageOverview(revision=1, text="已覆盖事实", coverage_spans=(span,)),
        }
    )
    if point:
        store.failure_hook = FailureInjector(point).hit
    return store, updated


@pytest.mark.parametrize("point", ["temp_created", "written", "flushed", "fsynced", "before_replace"])
def test_pre_replace_failure_keeps_entire_old_revision(tmp_path: Path, point: str) -> None:
    store, updated = prepared(tmp_path, point)
    with pytest.raises(OSError):
        store.commit(updated)
    recovered = LongTermStore(tmp_path, store.archive).load()
    assert recovered.revision == 0
    assert recovered.curation.curated_through_sequence == 0
    assert recovered.coverage_overview.coverage_spans == ()


def test_after_replace_failure_recovers_entire_new_revision(tmp_path: Path) -> None:
    store, updated = prepared(tmp_path, "after_replace")
    with pytest.raises(OSError):
        store.commit(updated)
    recovered = LongTermStore(tmp_path, store.archive).load()
    assert recovered.revision == 1
    assert recovered.curation.curated_through_sequence == 1
    assert len(recovered.coverage_overview.coverage_spans) == 1


def test_external_manual_edit_aborts_commit(tmp_path: Path) -> None:
    store, updated = prepared(tmp_path)
    store.load()
    store.path.write_text(
        store.path.read_text(encoding="utf-8") + "# [2026-07-19] 并发人工编辑\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception) as raised:
        store.commit(updated)
    assert getattr(raised.value, "code", None) == "CONCURRENT_MANUAL_EDIT"
