"""[2026-07-19] 单轮控制器的确认顺序决定用户可见内容是否可恢复。"""

from pathlib import Path

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.memory.archive import RawRecordArchive
from bai_agent.prompting.assembler import PromptAssembler
from bai_agent.runtime.controller import SingleTurnController
from bai_agent.states.resolver import StaticStateResolver
from tests.fakes import FakeProvider


@pytest.mark.asyncio
async def test_input_precedes_provider_and_output_precedes_stdout(tmp_path: Path) -> None:
    events: list[str] = []
    archive = RawRecordArchive(tmp_path)
    original_append = archive.append

    def append(**values):
        record = original_append(**values)
        events.append(f"saved:{record.role.value}")
        return record

    archive.append = append  # type: ignore[method-assign]  # [2026-07-19] 测试记录调用顺序。
    provider = FakeProvider(response="完整回答")
    original_complete = provider.complete

    async def complete(request):
        events.append("provider")
        return await original_complete(request)

    provider.complete = complete  # type: ignore[method-assign]  # [2026-07-19] 测试记录调用顺序。
    controller = SingleTurnController(
        archive,
        provider,
        StaticStateResolver.default(),
        PromptAssembler.mvp("基础人格", ("默认状态",)),
        on_output=lambda text: events.append(f"stdout:{text}"),
    )
    await controller.run_turn("用户输入")
    assert events == ["saved:user", "provider", "saved:assistant", "stdout:完整回答"]


@pytest.mark.asyncio
async def test_provider_failure_keeps_only_confirmed_user(tmp_path: Path) -> None:
    archive = RawRecordArchive(tmp_path)
    controller = SingleTurnController(
        archive,
        FakeProvider(failure=BaiError("PROVIDER_FAILED", "模型调用失败。", retryable=True)),
        StaticStateResolver.default(),
        PromptAssembler.mvp("基础人格", ("默认状态",)),
    )
    with pytest.raises(BaiError, match="模型调用失败"):
        await controller.run_turn("仍应保存")
    assert [item.role.value for item in archive.read_all()] == ["user"]
