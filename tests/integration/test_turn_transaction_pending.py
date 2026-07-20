"""[2026-07-20] 普通 provider 失败仅发布一个可恢复 USER pending。"""

from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.runtime.controller import SingleTurnController
from bai_agent.states.resolver import StaticStateResolver


class FailedGateway:
    def __init__(self, failure: BaiError | None = None) -> None:
        self.failure = failure or BaiError("PROVIDER_FAILED", "普通失败。", retryable=True)

    async def complete(self, _draft):
        raise self.failure


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        BaiError("PROVIDER_FAILED", "retry exhausted", retryable=True),
        BaiError("PROVIDER_FAILED", "non-retryable provider"),
        BaiError("NETWORK_UNAVAILABLE", "network interruption"),
    ],
)
async def test_provider_failure_is_single_pending_and_resumable(
    tmp_path: Path, failure: BaiError
) -> None:
    archive = RawRecordArchive(tmp_path)
    controller = SingleTurnController(
        archive, FailedGateway(failure), StaticStateResolver.default(), PromptAssembler.mvp("基础", ("状态",)),
        transaction_root=tmp_path,
    )
    with pytest.raises(BaiError):
        await controller.run_turn("待恢复输入")
    pending = archive.pending_turn()
    assert pending and pending.content == "待恢复输入"
    assert len(archive.read_all()) == 1

    assert controller.discard_pending(expected_turn_id=pending.turn_id) == pending.turn_id
    assert archive.read_all() == ()
