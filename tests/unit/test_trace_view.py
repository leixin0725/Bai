"""[2026-08-08] 虚拟化 TraceView 的行换行、后台构建、取消与渲染缓存。"""

import threading

import pytest

from rich.style import Style
from rich.text import Text

from textual.app import App, ComposeResult
from textual.strip import Strip

from bai_agent.debug.trace_view import TraceView, wrap_renderable


class _TraceApp(App[None]):
    def __init__(self, view: TraceView) -> None:
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        yield self.view


def test_wrap_renderable_splits_rows_and_keeps_row_styles() -> None:
    renderable = Text()
    renderable.append("普通" * 20)
    renderable.append("红色", style=Style(color="red"))
    rows, styles = wrap_renderable(renderable, width=10)
    assert len(rows) > 1
    assert "".join(rows) == "普通" * 20 + "红色"
    assert any(style is not None for style in styles)
    assert rows[-1].endswith("红色")


@pytest.mark.asyncio
async def test_trace_view_sync_content_sets_rows_and_virtual_size() -> None:
    view = TraceView()
    app = _TraceApp(view)
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        view.set_content(Text("第一行\n第二行"), background=False)
        await pilot.pause()
        assert view.plain_text == "第一行\n第二行"
        assert view.is_empty is False
        assert view.virtual_size.height == 2
        line = view.render_line(0)
        assert isinstance(line, Strip)
        assert "第一行" in line.text


@pytest.mark.asyncio
async def test_trace_view_background_build_shows_placeholder_then_ready() -> None:
    view = TraceView()
    app = _TraceApp(view)
    ready = False

    def on_ready() -> None:
        nonlocal ready
        ready = True

    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        view.set_content(
            Text("中文提示词。" * 5_000),
            background=True,
            ready_callback=on_ready,
        )
        assert view.is_loading
        for _ in range(100):
            await pilot.pause()
            if not view.is_loading:
                break
        assert not view.is_loading
        assert ready
        assert len(view._rows) > 100
        assert view.plain_text.startswith("中文提示词。")


@pytest.mark.asyncio
async def test_trace_view_clear_cancels_pending_build_and_releases_source() -> None:
    view = TraceView()
    app = _TraceApp(view)
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        view.set_content(Text("中文提示词。" * 5_000), background=True)
        token = view._build_token
        view.clear_content()
        await pilot.pause()
        assert view.is_empty
        assert view._source is None
        assert view._build_token != token
        for _ in range(20):
            await pilot.pause()
        assert view.is_empty


@pytest.mark.asyncio
async def test_trace_view_blank_lines_beyond_rows() -> None:
    view = TraceView()
    app = _TraceApp(view)
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        view.set_content(Text("一行"), background=False)
        assert view.render_line(1).text.strip() == ""
        assert view.render_line(5).text.strip() == ""


@pytest.mark.asyncio
async def test_trace_view_background_builds_are_serialized_and_latest_wins(monkeypatch) -> None:
    """[2026-08-08] 连续触发大内容后台构建时：同一时刻至多一个换行任务在跑，
    排队的旧请求被丢弃，最终内容为最新请求（回归多线程 GIL 争抢卡顿）。"""
    view = TraceView()
    app = _TraceApp(view)
    real_wrap = wrap_renderable
    entered = threading.Event()
    release = threading.Event()
    active = 0
    max_active = 0
    calls: list[str] = []
    state_lock = threading.Lock()

    def instrumented(renderable, width):
        nonlocal active, max_active
        text = renderable.plain
        with state_lock:
            active += 1
            max_active = max(max_active, active)
            calls.append(text)
        entered.set()
        assert release.wait(2)
        try:
            return real_wrap(renderable, width)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr("bai_agent.debug.trace_view.wrap_renderable", instrumented)
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        view.set_content(Text("第一"), background=True)
        assert entered.wait(2)
        view.set_content(Text("第二"), background=True)
        view.set_content(Text("第三"), background=True)
        release.set()
        for _ in range(200):
            await pilot.pause()
            if not view.is_loading:
                break
        assert not view.is_loading
        assert max_active == 1
        assert view.plain_text.startswith("第三")
        with state_lock:
            assert calls == ["第一", "第三"]
