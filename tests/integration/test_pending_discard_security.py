"""[2026-07-20] pending 丢弃在长期引用、coverage 或内容冲突时失败关闭且不泄露正文。"""

from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import (
    CoverageSpan,
    CurationCheckpoint,
    LongTermMemoryDocument,
    MemoryCoverageOverview,
    Role,
    new_id,
    utc_now,
)
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.long_term import LongTermStore
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.runtime.controller import SingleTurnController
from bai_agent.states.resolver import StaticStateResolver


REVISION = "sha256:" + "7" * 64


def _controller(root: Path) -> tuple[SingleTurnController, LongTermStore]:
    archive = RawRecordArchive(root)
    store = LongTermStore(root, archive)
    store.initialize()
    controller = SingleTurnController(
        archive, object(), StaticStateResolver.default(), PromptAssembler.mvp("基础", ("状态",)),
        long_term_store=store, transaction_root=root,
    )
    return controller, store


@pytest.mark.parametrize("reference_kind", ["memory", "coverage"])
def test_long_term_reference_blocks_pending_discard_without_modifying_files(
    tmp_path: Path, reference_kind: str
) -> None:
    controller, store = _controller(tmp_path)
    pending = controller.repository.append(
        role=Role.USER, content="private-pending-body", turn_id=new_id("turn"),
        state_id="default", config_revision=REVISION,
    )
    if reference_kind == "memory":
        store.initialize_with_manual_memory("synthetic-memory", (pending,))
    else:
        document = LongTermMemoryDocument(
            schema_version=1,
            revision=1,
            curation=CurationCheckpoint(
                curated_through_sequence=1, last_batch_id=new_id("batch"),
                updated_at=utc_now(), covered_record_ids=(pending.record_id,),
            ),
            coverage_overview=MemoryCoverageOverview(
                revision=1, text="synthetic coverage",
                coverage_spans=(CoverageSpan(
                    start_sequence=1, end_sequence=1, batch_id=new_id("batch"),
                    record_ids=(pending.record_id,), record_hashes=(pending.content_sha256,),
                ),),
            ),
            memories=(),
        )
        store.commit(document)

    raw_path = next((tmp_path / "raw").glob("*.jsonl"))
    before_raw = raw_path.read_bytes()
    before_long = store.path.read_bytes()
    with pytest.raises(BaiError) as caught:
        controller.discard_pending(expected_turn_id=pending.turn_id)
    assert caught.value.code == "MEMORY_PENDING_REFERENCED"
    assert "private-pending-body" not in caught.value.safe_message
    assert raw_path.read_bytes() == before_raw
    assert store.path.read_bytes() == before_long
