"""[2026-07-20] 记忆整理提案在轮次确认前不得修改长期记忆。"""

from pathlib import Path

from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.curation import ProposedCuration
from bai_agent.memory.long_term import LongTermStore


def test_proposal_is_pure_until_explicit_publish(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    store = LongTermStore(tmp_path, archive)
    baseline = store.initialize()
    proposal = ProposedCuration(target_document=baseline, source_record_ids=())
    assert store.load() == baseline
    assert proposal.target_document == baseline
