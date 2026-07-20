"""[2026-07-20] 普通 provider 失败仅发布一个可恢复 USER pending。"""

from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.runtime.controller import SingleTurnController
from bai_agent.states.resolver import StaticStateResolver


class FailedGateway:
    async def complete(self, _draft):
        raise BaiError("PROVIDER_FAILED", "普通失败。", retryable=True)


@pytest.mark.asyncio
async def test_provider_failure_is_single_pending_and_resumable(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    controller = SingleTurnController(
        archive, FailedGateway(), StaticStateResolver.default(), PromptAssembler.mvp("基础", ("状态",)),
        transaction_root=tmp_path,
    )
    with pytest.raises(BaiError):
        await controller.run_turn("待恢复输入")
    pending = archive.pending_turn()
    assert pending and pending.content == "待恢复输入"
    assert len(archive.read_all()) == 1
