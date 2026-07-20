"""[2026-07-19] 长期 YAML 保留人工注释，并在损坏时只读回退最近有效版本。"""

from pathlib import Path
from io import StringIO

import pytest
from ruamel.yaml import YAML

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import Role
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.long_term import LongTermStore
from bai_agent.memory.temporal import MemoryTemporalProjector


REVISION = "sha256:" + "1" * 64


def seeded_store(tmp_path: Path) -> tuple[LongTermStore, RawRecordArchive]:
    archive = RawRecordArchive(tmp_path)
    archive.append(
        role=Role.USER,
        content="需要长期保留的事实",
        turn_id="turn-00000000-0000-4000-8000-000000000001",
        state_id="default",
        config_revision=REVISION,
    )
    store = LongTermStore(tmp_path, archive)
    store.initialize()
    return store, archive


def test_yaml_comment_round_trip_and_plaintext(tmp_path: Path) -> None:
    store, _ = seeded_store(tmp_path)
    original = store.path.read_text(encoding="utf-8")
    store.path.write_text("# [2026-07-19] 人工维护注释\n" + original, encoding="utf-8")
    document = store.load()
    revision = document.revision + 1
    store.commit(
        document.model_copy(
            update={
                "revision": revision,
                "coverage_overview": document.coverage_overview.model_copy(update={"revision": revision}),
            }
        )
    )
    text = store.path.read_text(encoding="utf-8")
    assert "人工维护注释" in text
    assert "coverage_overview:" in text
    assert "schema_version:" in text


def test_invalid_primary_is_preserved_and_last_valid_is_read_only(tmp_path: Path) -> None:
    store, _ = seeded_store(tmp_path)
    valid = store.path.read_bytes()
    store.path.write_text("revision: [invalid", encoding="utf-8")
    invalid = store.path.read_bytes()
    fallback = store.load()
    assert fallback.revision == 0
    assert store.read_only
    assert store.path.read_bytes() == invalid
    assert store.last_valid_path.read_bytes() == valid
    with pytest.raises(BaiError) as raised:
        store.commit(fallback)
    assert raised.value.code == "MEMORY_READ_ONLY_FALLBACK"


def test_source_hash_is_validated(tmp_path: Path) -> None:
    store, archive = seeded_store(tmp_path)
    record = archive.read_all()[0]
    payload = store.load().model_dump(mode="json")
    payload["memories"] = [
        {
            "memory_id": "mem-00000000-0000-4000-8000-000000000001",
            "kind": "fact",
            "text": "事实",
            "status": "active",
            "source_refs": [{"record_id": record.record_id, "relation": "supports", "record_sha256": "sha256:" + "0" * 64}],
            "created_by": "manual",
            "created_at": "2026-07-19T00:00:00Z",
            "updated_at": "2026-07-19T00:00:00Z",
            "supersedes": [],
            "tags": [],
        }
    ]
    output = StringIO()
    YAML().dump(payload, output)
    store.path.write_text(output.getvalue(), encoding="utf-8")
    fallback = store.load()
    assert store.read_only
    assert fallback.memories == ()


def test_valid_manual_text_edit_is_marked_and_revisioned(tmp_path: Path) -> None:
    store, archive = seeded_store(tmp_path)
    created = store.initialize_with_manual_memory("原始人工事实", archive.read_all())
    data = YAML().load(store.path.read_text(encoding="utf-8"))
    data["memories"][0]["text"] = "人工修正后的事实"
    output = StringIO()
    YAML().dump(data, output)
    store.path.write_text(output.getvalue(), encoding="utf-8")
    loaded = store.load()
    assert loaded.revision == created.revision + 1
    assert loaded.memories[0].text == "人工修正后的事实"
    assert loaded.memories[0].created_by.value == "manual"


def test_temporal_projection_does_not_rewrite_existing_raw_or_yaml(tmp_path: Path) -> None:
    store, archive = seeded_store(tmp_path)
    document = store.initialize_with_manual_memory("事实", archive.read_all())
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    projector = MemoryTemporalProjector.from_records(archive.read_all())
    assert projector.project_memory(document.memories[0]).body == "事实"
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_unrecognized_primary_uses_valid_fallback_but_never_recorded_projection(tmp_path: Path) -> None:
    store, archive = seeded_store(tmp_path)
    store.path.write_text("schema_version: 0\n", encoding="utf-8")
    fallback = store.load()
    assert store.read_only
    assert fallback.schema_version == 1
    projector = MemoryTemporalProjector.from_records(archive.read_all())
    assert not hasattr(projector, "project_recorded")


def test_last_valid_recovery_still_rejects_broken_actual_sources(tmp_path: Path) -> None:
    store, archive = seeded_store(tmp_path)
    document = store.initialize_with_manual_memory("事实", archive.read_all())
    assert document.memories
    store.path.write_text("revision: [invalid", encoding="utf-8")

    class MissingArchive:
        def read_all(self):
            return ()

    store.archive = MissingArchive()
    with pytest.raises(BaiError) as raised:
        store.load()
    assert raised.value.code == "SOURCE_RECORD_MISSING"
