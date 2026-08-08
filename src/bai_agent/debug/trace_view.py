"""[2026-08-08] 虚拟化只读文本视图：按需换行、纯文本行存储、仅渲染可见行。"""

from __future__ import annotations

from collections.abc import Callable
import threading

from rich.console import Console
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

from textual.events import Resize
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip


PLACEHOLDER = "正在加载…"


def wrap_renderable(
    renderable: Text,
    width: int,
) -> tuple[list[str], list[Style | None]]:
    """把富文本按给定宽度换行为纯文本行与每行样式，供后台线程调用。"""
    console = Console(width=width, force_terminal=True, color_system=None, file=None)
    render_options = console.options.update_width(width)
    segments = console.render(renderable, render_options)
    rows: list[str] = []
    styles: list[Style | None] = []
    for line in Segment.split_lines(segments):
        rows.append("".join(segment.text for segment in line))
        styles.append(
            next(
                (segment.style for segment in line if segment.style is not None),
                None,
            )
        )
    return rows, styles


class TraceView(ScrollView, can_focus=True):
    """只读虚拟化文本视图。

    内容以“纯文本行 + 每行样式”保存，渲染时只构建可见行的 Strip，避免像
    RichLog 那样为整段大文本保留数百万样式单元格。大段内容可在后台线程
    换行，构建完成前显示占位符，界面保持可交互。
    """

    DEFAULT_CSS = """
    TraceView {
        scrollbar-gutter: stable;
    }
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._rows: list[str] = []
        self._row_styles: list[Style | None] = []
        self._row_cache: dict[int, Strip] = {}
        self._source: Text | None = None
        self._placeholder = False
        self._build_worker: threading.Thread | None = None
        self._build_token = 0
        self._ready_callback: Callable[[], None] | None = None
        self._last_width = 0
        self.virtual_size = Size(0, 0)

    @property
    def is_loading(self) -> bool:
        return self._placeholder

    @property
    def is_empty(self) -> bool:
        return not self._placeholder and not self._rows

    @property
    def plain_text(self) -> str:
        """当前已构建行的纯文本（占位/构建中为空）。"""
        return "\n".join(self._rows)

    def set_content(
        self,
        renderable: Text,
        *,
        background: bool = False,
        ready_callback: Callable[[], None] | None = None,
    ) -> None:
        """设置内容；background=True 时在后台线程换行并显示占位符。"""
        self._cancel_build()
        self._source = renderable
        self._ready_callback = ready_callback
        self._row_cache.clear()
        width = max(self.size.width, 1)
        self._last_width = width
        if not background:
            self._placeholder = False
            self._rows, self._row_styles = wrap_renderable(renderable, width)
            self.virtual_size = Size(width, len(self._rows))
            self.refresh()
            callback = self._ready_callback
            self._ready_callback = None
            if callback is not None:
                callback()
            return
        self._placeholder = True
        self._rows = []
        self._row_styles = []
        self.virtual_size = Size(width, 0)
        self.refresh()
        token = self._build_token + 1
        self._build_token = token
        app = self.app

        def build() -> None:
            rows, styles = wrap_renderable(renderable, width)
            try:
                app.call_from_thread(self._complete_build, token, rows, styles)
            except RuntimeError:
                # [2026-08-08] 应用已退出/事件循环已关闭时丢弃结果。
                pass

        self._build_worker = threading.Thread(
            target=build,
            name="trace-view-build",
            daemon=True,
        )
        self._build_worker.start()

    def clear_content(self) -> None:
        """取消后台构建并释放全部内容引用。"""
        self._cancel_build()
        self._source = None
        self._rows = []
        self._row_styles = []
        self._row_cache.clear()
        self._placeholder = False
        self._ready_callback = None
        self.virtual_size = Size(0, 0)
        self.refresh()

    def on_resize(self, event: Resize) -> None:
        if self._source is not None and event.size.width != self._last_width:
            self.set_content(self._source, background=True)

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        y = y + self.scroll_offset.y
        if self._placeholder:
            if y == 0:
                return Strip([Segment(PLACEHOLDER, self.rich_style)]).crop_extend(
                    0, width, self.rich_style
                )
            return Strip.blank(width, self.rich_style)
        if y >= len(self._rows):
            return Strip.blank(width, self.rich_style)
        cached = self._row_cache.get(y)
        if cached is not None:
            return cached
        style = self._row_styles[y] or self.rich_style
        strip = Strip([Segment(self._rows[y], style)]).crop_extend(
            0, width, self.rich_style
        )
        if len(self._row_cache) > 4096:
            self._row_cache.clear()
        self._row_cache[y] = strip
        return strip

    def _complete_build(
        self,
        token: int,
        rows: list[str],
        styles: list[Style | None],
    ) -> None:
        if token != self._build_token:
            return
        self._build_worker = None
        self._placeholder = False
        self._rows = rows
        self._row_styles = styles
        self._row_cache.clear()
        width = max(self.size.width, 1)
        self.virtual_size = Size(width, len(rows))
        self.refresh()
        callback = self._ready_callback
        self._ready_callback = None
        if callback is not None:
            callback()

    def _cancel_build(self) -> None:
        self._build_token += 1
        self._build_worker = None
