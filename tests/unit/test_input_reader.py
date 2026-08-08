"""[2026-08-08] BR-006 一次输入动作：管道 EOF 整批、TTY 缓冲连片合并、逐行独立，零等待。"""

from __future__ import annotations

import asyncio

import pytest

from bai_agent.domain.models import ConversationAction, InputBoundary
from bai_agent.runtime.input_reader import InputReader
from tests.fakes import FakeInputSource


async def collect(source) -> tuple[list[ConversationAction], int]:
    actions: list[ConversationAction] = []
    eof_calls = 0

    async def on_action(action: ConversationAction) -> None:
        actions.append(action)

    def on_eof() -> None:
        nonlocal eof_calls
        eof_calls += 1

    await InputReader(source, on_action=on_action, on_eof=on_eof).run()
    return actions, eof_calls


async def test_pipe_input_is_one_action_at_eof() -> None:
    actions, eof_calls = await collect(
        FakeInputSource(["第一行", "第二行", "第三行"], is_tty=False)
    )
    assert len(actions) == 1
    assert actions[0].text == "第一行\n第二行\n第三行"
    assert actions[0].source_boundary is InputBoundary.PIPE_EOF
    assert eof_calls == 1


async def test_tty_sequential_lines_are_independent_actions() -> None:
    actions, _ = await collect(
        FakeInputSource(["第一行", "第二行"], is_tty=True, buffered_indexes=set())
    )
    assert [action.text for action in actions] == ["第一行", "第二行"]
    assert all(
        action.source_boundary is InputBoundary.BUFFER_EMPTY for action in actions
    )


async def test_tty_buffered_paste_merges_until_buffer_empty() -> None:
    actions, _ = await collect(
        FakeInputSource(
            ["第一行", "第二行", "第三行"],
            is_tty=True,
            buffered_indexes={0, 1},
        )
    )
    assert len(actions) == 1
    assert actions[0].text == "第一行\n第二行\n第三行"
    assert actions[0].source_boundary is InputBoundary.BUFFER_EMPTY


async def test_blank_lines_are_preserved_inside_action() -> None:
    actions, _ = await collect(
        FakeInputSource(["第一段", "", "第二段"], is_tty=False)
    )
    assert len(actions) == 1
    assert actions[0].text == "第一段\n\n第二段"


async def test_whitespace_only_input_produces_no_action() -> None:
    actions, eof_calls = await collect(
        FakeInputSource(["", "   "], is_tty=False)
    )
    assert actions == []
    assert eof_calls == 1


@pytest.mark.asyncio
async def test_reader_zero_wait_never_sleeps() -> None:
    """[2026-08-08] 合并只依赖缓冲判定；即使缓冲判定全为 False 也不等待。"""
    started = asyncio.get_running_loop().time()
    actions, _ = await collect(
        FakeInputSource(["甲", "乙"], is_tty=True, buffered_indexes=set())
    )
    assert len(actions) == 2
    assert asyncio.get_running_loop().time() - started < 0.1
