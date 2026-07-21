"""[2026-07-19] 窗口整理只调用一次专用人格，并在联合提交成功后修剪。"""

import json
from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.domain.models import CompletionResult, Role
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.memory.curation import CurationPolicy, CurationService
from bai_agent.memory.long_term import LongTermStore
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.runtime.controller import SingleTurnController
from bai_agent.states.resolver import StaticStateResolver
from tests.fakes import FakeProvider


REVISION = "sha256:" + "1" * 64


class CuratorProvider:
    def __init__(self, candidate=True, failure=None, source_alias="r1") -> None:
        self.calls = []
        self.candidate = candidate
        self.failure = failure
        self.source_alias = source_alias

    async def complete(self, request):
        self.calls.append(request)
        if self.failure:
            raise self.failure
        payload = {
            "memory_candidates": (
                [{"kind": "user", "text": "稳定事实", "sources": [self.source_alias]}]
                if self.candidate
                else []
            ),
            "overview": "该批次已整理",
        }
        return CompletionResult(text=json.dumps(payload, ensure_ascii=False), finish_reason="stop")


def records(tmp_path: Path, turns: int):
    archive = RawRecordArchive(tmp_path)
    start = len(archive.read_all()) // 2
    for index in range(start, start + turns):
        turn = f"turn-00000000-0000-4000-8000-{index:012d}"
        archive.append(role=Role.USER, content=f"用户-{index}", turn_id=turn, state_id="default", config_revision=REVISION)
        archive.append(role=Role.ASSISTANT, content=f"助手-{index}", turn_id=turn, state_id="default", config_revision=REVISION)
    return archive


@pytest.mark.asyncio
async def test_threshold_zero_call_then_single_joint_commit(tmp_path: Path) -> None:
    archive = records(tmp_path, 2)
    store = LongTermStore(tmp_path, archive)
    store.initialize()
    provider = CuratorProvider()
    service = CurationService(
        archive, store, provider,
        CurationPolicy(max_records=6, reserved_records=2, min_batch_records=2, max_batch_records=4),
        curator_persona="整理人格", prompt_template="$batch_records", config_revision=REVISION,
    )
    assert await service.curate_if_needed() is None
    assert provider.calls == []

    archive = records(tmp_path, 2)  # [2026-07-19] 同一根增加到四个完整轮次。
    result = await service.curate_if_needed()
    assert result is not None
    assert len(provider.calls) == 1
    document = store.load()
    assert document.revision == document.coverage_overview.revision
    assert document.curation.curated_through_sequence == document.coverage_overview.coverage_spans[-1].end_sequence
    assert len(document.memories[0].source_refs) == 1
    assert document.memories[0].source_refs[0].record_id == document.coverage_overview.coverage_spans[0].record_ids[0]
    prompt = provider.calls[0].messages[1].content
    assert document.memories[0].source_refs[0].record_id not in prompt
    assert "sha256:" not in prompt
    assert provider.calls[0].metadata == {}
    restarted_provider = CuratorProvider()
    restarted = CurationService(
        archive, LongTermStore(tmp_path, archive), restarted_provider,
        CurationPolicy(max_records=6, reserved_records=2, min_batch_records=2, max_batch_records=4),
        curator_persona="整理人格", prompt_template="$batch_records", config_revision=REVISION,
    )
    assert await restarted.curate_if_needed() is None
    assert restarted_provider.calls == []


@pytest.mark.asyncio
async def test_empty_extraction_advances_coverage_and_failure_does_not(tmp_path: Path) -> None:
    archive = records(tmp_path, 4)
    store = LongTermStore(tmp_path, archive)
    store.initialize()
    policy = CurationPolicy(max_records=6, reserved_records=2, min_batch_records=2, max_batch_records=4)
    empty = CurationService(archive, store, CuratorProvider(candidate=False), policy, curator_persona="整理", prompt_template="$batch_records", config_revision=REVISION)
    await empty.curate_if_needed()
    document = store.load()
    assert document.memories == ()
    assert document.coverage_overview.coverage_spans

    old_frontier = document.curation.curated_through_sequence
    failed = CurationService(archive, store, CuratorProvider(failure=BaiError("PROVIDER_FAILED", "整理失败。")), policy, curator_persona="整理", prompt_template="$batch_records", config_revision=REVISION)
    with pytest.raises(BaiError):
        await failed.curate_if_needed(force=True)
    assert store.load().curation.curated_through_sequence == old_frontier


@pytest.mark.asyncio
async def test_unknown_short_source_alias_is_rejected_without_advancing_coverage(tmp_path: Path) -> None:
    archive = records(tmp_path, 4)
    store = LongTermStore(tmp_path, archive)
    baseline = store.initialize()
    service = CurationService(
        archive,
        store,
        CuratorProvider(source_alias="r999"),
        CurationPolicy(max_records=6, reserved_records=2, min_batch_records=2, max_batch_records=4),
        curator_persona="整理",
        prompt_template="$batch_records",
        config_revision=REVISION,
    )

    with pytest.raises(BaiError) as raised:
        await service.curate_if_needed()

    assert raised.value.code == "CURATION_SOURCE_INVALID"
    assert store.load() == baseline


@pytest.mark.asyncio
async def test_exact_existing_memory_is_not_added_again_but_batch_is_covered(tmp_path: Path) -> None:
    archive = records(tmp_path, 4)
    store = LongTermStore(tmp_path, archive)
    store.initialize_with_manual_memory("稳定事实", (archive.read_all()[0],))
    service = CurationService(
        archive,
        store,
        CuratorProvider(),
        CurationPolicy(max_records=6, reserved_records=2, min_batch_records=2, max_batch_records=4),
        curator_persona="整理",
        prompt_template="$batch_records",
        config_revision=REVISION,
    )

    await service.curate_if_needed()

    document = store.load()
    assert [item.text for item in document.memories] == ["稳定事实"]
    assert document.coverage_overview.coverage_spans


@pytest.mark.asyncio
async def test_curation_failure_stops_chat_provider_after_user_is_saved(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    chat_provider = FakeProvider()

    class FailingCuration:
        async def curate_if_needed(self):
            raise BaiError("CURATION_FAILED", "整理失败。")

    controller = SingleTurnController(
        archive,
        chat_provider,
        StaticStateResolver.default(),
        PromptAssembler.mvp("基础人格", ("默认状态",)),
        curation_service=FailingCuration(),
    )
    with pytest.raises(BaiError):
        await controller.run_turn("仍需先保存的输入", config_revision=REVISION)
    assert len(archive.read_all()) == 1
    assert chat_provider.requests == []
