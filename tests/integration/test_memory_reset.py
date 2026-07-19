"""[2026-07-19] 重置分别验证全量出厂语义、仅派生长期记忆语义和单写者互斥。"""

from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CoverageSpan,
    CurationCheckpoint,
    LongTermMemoryDocument,
    MemoryCoverageOverview,
    Role,
)
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.long_term import LongTermStore
from bai_agent.memory.recovery import WriterLease
from bai_agent.memory.reset import MemoryResetService


REVISION = "sha256:" + "9" * 64


def _seed(memory_root: Path) -> tuple[RawRecordArchive, LongTermStore]:
    archive = RawRecordArchive(memory_root, segment_max_records=2)
    for index in range(3):
        turn_id = f"turn-00000000-0000-4000-8000-{index:012d}"
        archive.append(
            role=Role.USER,
            content=f"用户事实-{index}",
            turn_id=turn_id,
            state_id="default",
            config_revision=REVISION,
        )
        archive.append(
            role=Role.ASSISTANT,
            content=f"助手确认-{index}",
            turn_id=turn_id,
            state_id="default",
            config_revision=REVISION,
        )
    store = LongTermStore(memory_root, archive)
    store.initialize()
    store.initialize_with_manual_memory("需要被清除的长期事实", (archive.read_all()[0],))
    return archive, store


def _add_coverage(archive: RawRecordArchive, store: LongTermStore) -> LongTermMemoryDocument:
    document = store.load()
    records = archive.read_all()[:2]
    revision = document.revision + 1
    span = CoverageSpan(
        start_sequence=1,
        end_sequence=2,
        batch_id="batch-00000000-0000-4000-8000-000000000001",
        record_ids=tuple(record.record_id for record in records),
        record_hashes=tuple(record.content_sha256 for record in records),
    )
    return store.commit(
        LongTermMemoryDocument(
            schema_version=1,
            revision=revision,
            curation=CurationCheckpoint(
                curated_through_sequence=2,
                last_batch_id=span.batch_id,
                updated_at=records[-1].created_at,
                covered_record_ids=span.record_ids,
            ),
            coverage_overview=MemoryCoverageOverview(
                revision=revision,
                text="包含需要被清除的长期事实。",
                coverage_spans=(span,),
            ),
            memories=document.memories,
        )
    )


def test_reset_long_term_preserves_raw_and_coverage_index(tmp_path: Path) -> None:
    memory_root = tmp_path / "data" / "memory"
    archive, store = _seed(memory_root)
    before = _add_coverage(archive, store)
    raw_before = archive.read_all()
    store.path.write_text(
        "# 需要被清除的长期注释\n" + store.path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    store.load()

    report = MemoryResetService(memory_root, segment_max_records=2).reset("long-term")
    after = LongTermStore(memory_root, RawRecordArchive(memory_root)).load()

    assert report.scope == "long-term"
    assert archive.read_all() == raw_before
    assert after.memories == ()
    assert after.curation == before.curation
    assert after.coverage_overview.coverage_spans == before.coverage_overview.coverage_spans
    assert "需要被清除" not in after.coverage_overview.text
    assert "需要被清除" not in store.path.read_text(encoding="utf-8")
    assert "需要被清除" not in store.last_valid_path.read_text(encoding="utf-8")
    assert after.revision == before.revision + 1


def test_reset_all_returns_memory_to_factory_state_and_preserves_security_state(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    memory_root = data_root / "memory"
    archive, store = _seed(memory_root)
    _add_coverage(archive, store)
    security_state = data_root / ".security" / "incident.json"
    security_state.parent.mkdir(parents=True)
    security_state.write_text("保留安全事件状态", encoding="utf-8")
    for temporary in (
        memory_root / ".long_term.yaml.crash.tmp-atomic",
        memory_root / ".state" / ".long_term.last-valid.yaml.crash.tmp-atomic",
        memory_root / "raw" / ".00000003.jsonl.crash.tmp-atomic",
    ):
        temporary.write_text("需要被清除的残留记忆", encoding="utf-8")

    report = MemoryResetService(memory_root, segment_max_records=2).reset("all")
    restarted_archive = RawRecordArchive(memory_root)
    restarted = LongTermStore(memory_root, restarted_archive).load()

    assert report.scope == "all"
    assert report.raw_records_before == 6
    assert report.raw_records_after == 0
    assert restarted_archive.read_all() == ()
    assert restarted == LongTermMemoryDocument.empty()
    assert security_state.read_text(encoding="utf-8") == "保留安全事件状态"
    assert list((memory_root / "raw").glob("*.jsonl")) == []
    assert list(memory_root.rglob("*.tmp-atomic")) == []


def test_reset_refuses_to_run_while_chat_writer_lock_is_held(tmp_path: Path) -> None:
    memory_root = tmp_path / "data" / "memory"
    _seed(memory_root)
    lease = WriterLease(memory_root / ".state" / "writer.lock")
    lease.acquire()
    try:
        with pytest.raises(BaiError) as captured:
            MemoryResetService(memory_root).reset("all")
        assert captured.value.code == "WRITER_LOCKED"
    finally:
        lease.release()


def test_reset_all_recovers_corrupted_authoritative_memory(tmp_path: Path) -> None:
    memory_root = tmp_path / "data" / "memory"
    archive, store = _seed(memory_root)
    last_segment = sorted((memory_root / "raw").glob("*.jsonl"))[-1]
    last_segment.write_bytes(last_segment.read_bytes() + b"incomplete")
    store.path.write_text("不是有效 YAML：[", encoding="utf-8")
    store.last_valid_path.write_text("同样无效：[", encoding="utf-8")

    report = MemoryResetService(memory_root, segment_max_records=2).reset("all")

    assert report.raw_records_before == 7
    assert report.long_term_items_before == -1
    assert RawRecordArchive(memory_root).read_all() == ()
    assert LongTermStore(memory_root, RawRecordArchive(memory_root)).load() == (
        LongTermMemoryDocument.empty()
    )


def test_reset_all_delete_interruption_leaves_valid_raw_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory_root = tmp_path / "data" / "memory"
    archive, _ = _seed(memory_root)
    segments = sorted((memory_root / "raw").glob("*.jsonl"))
    original_unlink = Path.unlink

    def fail_on_second_newest(path: Path, *args, **kwargs) -> None:
        if path == segments[-2]:
            raise OSError("受控删除故障")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_on_second_newest)
    with pytest.raises(OSError):
        MemoryResetService(memory_root, segment_max_records=2).reset("all")

    remaining = RawRecordArchive(memory_root).read_all()
    assert [record.global_sequence for record in remaining] == [1, 2, 3, 4]
    assert LongTermStore(memory_root, RawRecordArchive(memory_root)).load() == (
        LongTermMemoryDocument.empty()
    )


def test_reset_rejects_unknown_scope_without_writes(tmp_path: Path) -> None:
    memory_root = tmp_path / "data" / "memory"
    archive, _ = _seed(memory_root)
    before = archive.read_all()
    with pytest.raises(BaiError) as captured:
        MemoryResetService(memory_root).reset("recent-only")
    assert captured.value.code == "MEMORY_RESET_SCOPE_INVALID"
    assert archive.read_all() == before
