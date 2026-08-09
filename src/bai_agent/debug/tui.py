"""[2026-07-20] Textual 批准界面以大纲+详情双栏审计提示词构建，
并在发送前释放正文来源。"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Footer, Label, ListItem, ListView, Static

from bai_agent.debug.trace_view import TraceView
from bai_agent.domain.errors import DebugPresentationError, TurnInterrupted
from bai_agent.domain.models import (
    ApprovalDecision,
    ContextUsageEstimate,
    MaterializedSendPayload,
    PreparedProviderRequest,
    TrustLevel,
    canonical_json,
    thaw_json,
)


SOURCE_PALETTE = {
    "config_file": "#91a3ad",
    "data_file": "#9baa96",
    "runtime": "#b2a18d",
    "generated": "#a89caf",
}

# [2026-07-21] 每个 message 使用固定莫兰迪色相；同组两色只改变明度，不使用随机 hash。
MESSAGE_PALETTE = (
    ("#aebdca", "#91a8b8"),
    ("#c2aaa7", "#aa918e"),
    ("#aebba5", "#91a48c"),
    ("#c0b095", "#aa987b"),
    ("#b5abc2", "#9d91ad"),
    ("#9fb9bb", "#83a2a5"),
    ("#b9aa9b", "#a18f80"),
    ("#aeb2bd", "#949aa8"),
)

_MESSAGE_POINTER = re.compile(r"^/messages/(\d+)(?:/|$)")
_MESSAGE_PART_ID = re.compile(r"(?:^|:)message:(\d+)(?::|$)")
_RECORD_ID = re.compile(
    r"rec-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# [2026-08-08] 除保留的换行/制表符外，其余控制字符均需转为可见转义；
# 使用 C 级 re.sub 替代逐字符 Python 循环，大载荷下避免纯 Python 开销。
_CONTROL_ESCAPE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def message_index_for_part(part) -> int | None:
    pointer = _MESSAGE_POINTER.match(part.payload_pointer or "")
    if pointer:
        return int(pointer.group(1))
    part_id = _MESSAGE_PART_ID.search(part.part_id)
    return int(part_id.group(1)) if part_id else None


def record_id_for_part(part) -> str | None:
    match = _RECORD_ID.search(part.part_id)
    return match.group(0) if match else None


def message_colors(message_index: int) -> tuple[str, str]:
    return MESSAGE_PALETTE[message_index % len(MESSAGE_PALETTE)]


def record_ordinals_for_parts(parts) -> dict[tuple[int, str], int]:
    ordinals: dict[tuple[int, str], int] = {}
    next_record: dict[int, int] = {}
    for part in parts:
        message_index = message_index_for_part(part)
        record_id = record_id_for_part(part)
        if message_index is None or record_id is None:
            continue
        key = (message_index, record_id)
        if key not in ordinals:
            ordinals[key] = next_record.get(message_index, 0)
            next_record[message_index] = ordinals[key] + 1
    return ordinals


def color_for_part(part, record_ordinals: dict[tuple[int, str], int]) -> str | None:
    message_index = message_index_for_part(part)
    if message_index is None:
        return None
    variants = message_colors(message_index)
    record_id = record_id_for_part(part)
    variant = (
        record_ordinals[(message_index, record_id)] % 2
        if record_id is not None and (message_index, record_id) in record_ordinals
        else 0
    )
    return variants[variant]


def escaped_whitespace(value: str) -> str:
    escaped = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\v": "\\v", "\f": "\\f", " ": "\\x20"}
    return "".join(
        escaped.get(character, f"\\u{ord(character):04x}")
        for character in value
    )


def preflight_debug_terminal(stdin=None, stdout=None) -> None:
    """[2026-07-20] 在构建应用和写 journal 前验证调试终端及 Textual 入口可用。"""
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    if not (
        callable(getattr(input_stream, "isatty", None))
        and callable(getattr(output_stream, "isatty", None))
        and input_stream.isatty()
        and output_stream.isatty()
    ):
        from bai_agent.domain.errors import BaiError

        raise BaiError(
            "DEBUG_TTY_REQUIRED",
            "提示调试要求 stdin/stdout 均为交互式 TTY；请移除重定向后重试。",
        )

    class TerminalProbeApp(App[None]):
        def on_mount(self) -> None:
            self.exit()

    try:
        TerminalProbeApp().run()
    except Exception as exc:
        raise DebugPresentationError() from exc


def resolve_color_enabled(
    policy: str,
    *,
    environ: dict[str, str] | os._Environ[str] | None = None,
    supports_color: bool,
) -> bool:
    values = os.environ if environ is None else environ
    if policy == "never":
        return False
    if policy == "always":
        return supports_color
    return supports_color and "NO_COLOR" not in values


def safe_terminal_text(value: str) -> str:
    """[2026-07-20] 控制字符变为可见文本，不能伪造边界、颜色或操作标签。"""
    return _CONTROL_ESCAPE.sub(lambda match: f"\\x{ord(match.group()):02x}", value)


# [2026-08-09] 大纲预览的信任提示色：低饱和绿/红；无颜色模式不加文字标签，
# 信任级别在选中后的详情标题中可见。
TRUSTED_PREVIEW_COLOR = "#8aa888"
UNTRUSTED_PREVIEW_COLOR = "#c08d88"
PREVIEW_MAX_CHARS = 64


def trust_preview_color(trust: TrustLevel) -> str:
    return (
        TRUSTED_PREVIEW_COLOR
        if trust is not TrustLevel.UNTRUSTED_DATA
        else UNTRUSTED_PREVIEW_COLOR
    )


def preview_text(value: str, limit: int = PREVIEW_MAX_CHARS) -> str:
    """[2026-08-09] 大纲单行预览：空白序列折叠为一个空格、控制字符转义、按字符截断。"""
    collapsed = " ".join(value.split())
    visible = safe_terminal_text(collapsed)
    if len(visible) > limit:
        visible = visible[:limit] + "…"
    return visible


class PromptApprovalApp(App[ApprovalDecision]):
    CSS = """
    Screen { layout: vertical; }
    #identity, #usage, #warning { height: auto; padding: 0 1; }
    #body { height: 1fr; }
    #outline { width: 30; border: solid $accent; }
    #outline ListItem { height: 1; }
    #outline Label { overflow: hidden; }
    #detail { width: 1fr; border: solid $accent; }
    #actions { height: 3; align: center middle; }
    Button { margin: 0 2; }
    """
    BINDINGS = [
        Binding("a", "approve", "批准并发送", priority=True),
        Binding("c", "copy_trace", "复制框内全部内容", priority=True),
        Binding("w", "toggle_untrusted", "展开/折叠不可信片段", priority=True),
        Binding("r", "reject", "拒绝并撤销整轮", priority=True),
        Binding("escape", "reject", "拒绝", priority=True),
        Binding("ctrl+c", "interrupt", "拒绝并退出", priority=True),
        Binding("tab", "toggle_focus", "切换大纲/详情焦点", priority=True),
        Binding("j", "cursor_down", "下移", priority=False),
        Binding("k", "cursor_up", "上移", priority=False),
    ]

    PAYLOAD_KEY = "payload"
    USAGE_KEY = "usage"
    # [2026-08-08] 详情超过该字符数时后台线程换行，界面先显示占位符。
    DETAIL_BACKGROUND_THRESHOLD = 60_000

    def __init__(
        self,
        request: PreparedProviderRequest,
        payload: MaterializedSendPayload,
        estimate: ContextUsageEstimate,
        *,
        warning: str = "本地界面可能显示私人记忆；复制会写入终端剪贴板，原始追踪不会由应用保存。",
        color_policy: str = "auto",
    ) -> None:
        super().__init__()
        self.request: PreparedProviderRequest | None = request
        self.payload: MaterializedSendPayload | None = payload
        self.estimate: ContextUsageEstimate | None = estimate
        self.warning = warning
        self.color_policy = color_policy
        self.decision: ApprovalDecision | None = None
        self.display_ready = False
        self.interrupted = False
        # [2026-08-09] 折叠状态：大纲默认隐藏不可信片段；展开时同时显示来源明细（沿用原行为）。
        self.expand_untrusted = False
        # [2026-08-08] 缓存 payload JSON、两种审计渲染结果与复制纯文本，
        # 避免每次布局、切换或复制都重建整段大文本。
        self._payload_json: str | None = None
        self._trace_collapsed: Text | None = None
        self._trace_expanded: Text | None = None
        self._audit_text: str | None = None
        self._ordinals_cache: dict[tuple[int, str], int] | None = None
        self._parts_by_order: dict[int, Any] | None = None
        self._outline_keys: list[str] = []
        self._selected_key: str | None = None

    def compose(self) -> ComposeResult:
        assert self.request is not None and self.payload is not None and self.estimate is not None
        request = self.request
        yield Static(
            f"调用 {request.call_sequence} / attempt {request.attempt} · {request.purpose} · "
            f"persona={request.persona_id or 'unknown'} · state={request.state_id or 'unknown'}\n"
            f"turn={request.turn_id} · flow={request.flow_id} · status=waiting_approval\n"
            f"provider={request.provider_id} · model={request.model} · config={request.config_revision}",
            id="identity",
            markup=False,
        )
        yield Static(self.warning, id="warning", markup=False)
        yield Static(self._usage_text(), id="usage", markup=False)
        # [2026-08-08] 两栏审计视图：左侧 part 大纲，右侧选中项详情；
        # 默认只渲染大纲与初始详情，正文按选择按需加载。
        with Horizontal(id="body"):
            yield ListView(id="outline")
            yield TraceView(id="detail")
        with Horizontal(id="actions"):
            yield Button("批准并发送 [A]", id="approve", variant="success", disabled=True)
            yield Button("复制框内全部内容 [C]", id="copy")
            yield Button("拒绝并撤销整轮 [R]", id="reject", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        # [2026-08-08] 首帧先展示身份/估算/操作区；首帧后构建大纲并选中
        # 估算明细（小内容同步换行），批准按钮随其就绪启用。
        self._rebuild_outline()
        self.call_after_refresh(self._initial_select)

    def _initial_select(self) -> None:
        self._select_outline(1, force_sync=True)
        self.call_after_refresh(self._mark_display_ready)
        self.query_one("#outline", ListView).focus()

    def _mark_display_ready(self) -> None:
        self.display_ready = True
        self.query_one("#approve", Button).disabled = False

    def _rebuild_outline(self) -> None:
        assert self.request is not None and self.estimate is not None
        color_enabled = self._color_enabled()
        entries: list[tuple[str, Text | str]] = [
            (self.PAYLOAD_KEY, "[P] 最终 provider 载荷（完整 JSON）"),
            (
                self.USAGE_KEY,
                f"[U] 上下文分段估算（{len(self.estimate.part_tokens)} 项）",
            ),
        ]
        parts_by_order: dict[int, Any] = {}
        for part in self.request.parts:
            parts_by_order[part.order] = part
            # [2026-08-09] 空白/空内容片段不进大纲；不可信片段默认折叠，按 w 切换。
            if not part.content.strip():
                continue
            if part.trust is TrustLevel.UNTRUSTED_DATA and not self.expand_untrusted:
                continue
            message_index = message_index_for_part(part)
            message_label = f"m{message_index}" if message_index is not None else "m?"
            label = Text(
                f"{message_label} · {preview_text(part.content)}",
                style=trust_preview_color(part.trust) if color_enabled else None,
            )
            entries.append((f"part:{part.order}", label))
        self._parts_by_order = parts_by_order
        self._outline_keys = [key for key, _ in entries]
        outline = self.query_one("#outline", ListView)
        outline.clear()
        for _, label in entries:
            outline.append(ListItem(Label(label)))

    def _select_outline(self, index: int, *, force_sync: bool = False) -> None:
        if not self._outline_keys:
            return
        index = max(0, min(index, len(self._outline_keys) - 1))
        key = self._outline_keys[index]
        self._selected_key = key
        self.query_one("#outline", ListView).index = index
        renderable = self._detail_renderable(key)
        if renderable is None:
            return
        background = (
            not force_sync
            and len(renderable.plain) > self.DETAIL_BACKGROUND_THRESHOLD
        )
        self.query_one("#detail", TraceView).set_content(
            renderable,
            background=background,
        )

    def _detail_renderable(self, key: str) -> Text | None:
        color_enabled = self._color_enabled()
        header_style = "bold" if color_enabled else None
        if key == self.PAYLOAD_KEY:
            rendered = Text()
            rendered.append("[最终 provider 载荷]\n", style=header_style)
            rendered.append(safe_terminal_text(self._payload_json_text()))
            return rendered
        if key == self.USAGE_KEY:
            rendered = Text()
            rendered.append("[上下文分段估算]\n", style=header_style)
            rendered.append(self._usage_details_text())
            return rendered
        if key.startswith("part:"):
            part = self._parts_by_order.get(int(key[len("part:") :]))
            if part is not None:
                rendered = Text()
                self._append_part_block(
                    rendered,
                    part,
                    expanded=self.expand_untrusted,
                    color_enabled=color_enabled,
                    record_ordinals=self._record_ordinals(),
                )
                return rendered
        return None

    def _color_enabled(self) -> bool:
        return resolve_color_enabled(
            self.color_policy,
            environ=os.environ,
            supports_color=getattr(self.console, "color_system", None) is not None,
        )

    def _record_ordinals(self) -> dict[tuple[int, str], int]:
        if self._ordinals_cache is None:
            assert self.request is not None
            self._ordinals_cache = record_ordinals_for_parts(self.request.parts)
        return self._ordinals_cache

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if self.request is None:
            return
        index = self.query_one("#outline", ListView).index
        if index is not None:
            self._select_outline(index)

    def _payload_json_text(self) -> str:
        if self._payload_json is None:
            assert self.payload is not None
            self._payload_json = canonical_json(thaw_json(self.payload.sdk_kwargs))
        return self._payload_json

    def _usage_text(self) -> str:
        assert self.estimate is not None
        if self.estimate.status != "estimated":
            return f"上下文：不可估算（{self.estimate.reason}）· 输出预留={self.estimate.max_output_tokens}"
        capacity = self.estimate.context_capacity if self.estimate.context_capacity is not None else "未知"
        percent = f"{self.estimate.projected_percent:.1f}%" if self.estimate.projected_percent is not None else "未知"
        remaining = self.estimate.projected_remaining_tokens if self.estimate.projected_remaining_tokens is not None else "未知"
        if self.estimate.part_tokens:
            main_id, main_tokens = max(self.estimate.part_tokens.items(), key=lambda item: item[1])
            main = f"{main_id}≈{main_tokens}"
        else:
            main = f"协议开销≈{self.estimate.protocol_overhead_tokens}"
        return (
            f"上下文：输入≈{self.estimate.estimated_input_tokens} + 输出预留{self.estimate.max_output_tokens} "
            f"= 峰值{self.estimate.projected_peak_tokens} / 容量{capacity} ({percent}) "
            f"剩余={remaining} [{self.estimate.risk}]\n"
            f"分段={len(self.estimate.part_tokens)}项（完整明细见下框）；"
            f"协议开销≈{self.estimate.protocol_overhead_tokens}；主要输入={main}"
        )

    def _usage_details_text(self) -> str:
        """[2026-07-21] 返回滚动框内的完整估算明细，避免顶栏随片段数无限增高。"""
        assert self.estimate is not None
        if self.estimate.status != "estimated":
            return (
                f"不可估算（{self.estimate.reason}）\n"
                f"输出预留={self.estimate.max_output_tokens}"
            )
        lines = [
            f"输入≈{self.estimate.estimated_input_tokens}；"
            f"输出预留={self.estimate.max_output_tokens}；"
            f"峰值={self.estimate.projected_peak_tokens}",
            f"协议开销≈{self.estimate.protocol_overhead_tokens}",
        ]
        lines.extend(
            f"{safe_terminal_text(part_id)}≈{tokens}"
            for part_id, tokens in self.estimate.part_tokens.items()
        )
        return "\n".join(lines)

    def _trace_renderable(self, *, expand_untrusted: bool | None = None) -> Text:
        assert self.request is not None and self.payload is not None and self.estimate is not None
        expanded = self.expand_untrusted if expand_untrusted is None else expand_untrusted
        cached = self._trace_expanded if expanded else self._trace_collapsed
        if cached is not None:
            return cached
        color_enabled = self._color_enabled()
        header_style = "bold" if color_enabled else None
        rendered = Text()
        rendered.append("[最终 provider 载荷]\n", style=header_style)
        rendered.append(safe_terminal_text(self._payload_json_text()))
        rendered.append(
            "\n\n[图例]\n"
            "状态：included=进入最终载荷；excluded=明确排除；empty=空片段；"
            "unknown_source=来源无法确认。\n"
            "信任：trusted_instruction=可信指令；trusted_metadata=可信元数据；"
            "user_instruction=当前用户指令；"
            "untrusted_data=不可信数据。\n"
            "message:N=N 为最终 provider messages 的零基索引；"
            "entity_ids=来源关联的实体 UUID/标识，不是聊天顺序编号。",
            style=header_style,
        )
        record_ordinals = self._record_ordinals()
        for part in self.request.parts:
            # [2026-07-21] 折叠模式隐藏整个空白 part，并隐藏其余 part 的来源明细；
            # [2026-08-09] 折叠模式同样隐藏不可信 part；
            # 展开/复制仍保留完整审计块。
            if (bool(part.content) and part.content.isspace()) and not expanded:
                continue
            if part.trust is TrustLevel.UNTRUSTED_DATA and not expanded:
                continue
            self._append_part_block(
                rendered,
                part,
                expanded=expanded,
                color_enabled=color_enabled,
                record_ordinals=record_ordinals,
            )
        rendered.append("\n\n[上下文分段估算]\n", style=header_style)
        rendered.append(self._usage_details_text())
        if expanded:
            self._trace_expanded = rendered
        else:
            self._trace_collapsed = rendered
        return rendered

    def _append_part_block(
        self,
        rendered: Text,
        part,
        *,
        expanded: bool,
        color_enabled: bool,
        record_ordinals: dict[tuple[int, str], int],
    ) -> None:
        """[2026-08-08] 追加单个 part 的审计块，详情视图与完整复制文本共用。"""
        whitespace_only = bool(part.content) and part.content.isspace()
        message_index = message_index_for_part(part)
        part_style = color_for_part(part, record_ordinals) if color_enabled else None
        message_label = f"message={message_index}" if message_index is not None else "message=无"
        rendered.append(
            f"\n\n[{part.part_id}] 状态={part.participation.value} "
            f"信任={part.trust.value} 来源数={len(part.sources)} {message_label}\n",
            style=part_style,
        )
        if whitespace_only:
            visible_content = escaped_whitespace(part.content)
        else:
            visible_content = safe_terminal_text(part.content)
        rendered.append(visible_content, style=part_style)
        if expanded:
            for source_index, source in enumerate(part.sources, start=1):
                location = source.project_relative_path or "无"
                entity_ids = ",".join(source.entity_ids) or "无"
                digest = source.content_sha256 or "无"
                revision = source.revision or "无"
                rendered.append(
                    f"\n  来源 {source_index}\n"
                    f"    类型={source.source_kind.value}\n"
                    f"    路径={location}\n"
                    f"    source_id={safe_terminal_text(source.source_id)}\n"
                    f"    producer={safe_terminal_text(source.producer)}\n"
                    f"    entity_ids={safe_terminal_text(entity_ids)}\n"
                    f"    sha256={safe_terminal_text(digest)}\n"
                    f"    revision={safe_terminal_text(revision)}",
                    style=(f"dim {SOURCE_PALETTE[source.source_kind.value]}" if color_enabled else None),
                )
        if part.exclusion_reason:
            rendered.append(f"\n  原因 {part.exclusion_reason}")

    def _trace_text(self) -> str:
        """[2026-07-21] 复制始终展开空白为可逆转义，不携带 Rich 颜色。"""
        if self._audit_text is None:
            self._audit_text = self._trace_renderable(expand_untrusted=True).plain
        return self._audit_text

    def _finish(self, approve: bool) -> None:
        if self.payload is None:
            return
        if approve and not self.display_ready:
            return
        payload = self.payload
        self.decision = ApprovalDecision.approve(payload) if approve else ApprovalDecision.reject(payload)
        # [2026-07-20] 决定生成后立即清空界面持有的正文、来源与载荷引用，再退出全屏。
        self.request = None
        self.payload = None
        self.estimate = None
        self._payload_json = None
        self._trace_collapsed = None
        self._trace_expanded = None
        self._audit_text = None
        self._ordinals_cache = None
        self._parts_by_order = None
        self._outline_keys = []
        self._selected_key = None
        self.query_one("#outline", ListView).clear()
        self.query_one("#detail", TraceView).clear_content()
        self.exit(self.decision)

    def action_approve(self) -> None:
        self._finish(True)

    def action_reject(self) -> None:
        self._finish(False)

    def action_copy_trace(self) -> None:
        if self.request is None or self.payload is None:
            return
        self.copy_to_clipboard(self._trace_text())
        self.notify("已复制框内全部内容", timeout=2)

    def action_toggle_untrusted(self) -> None:
        if self.request is None or self.payload is None:
            return
        self.expand_untrusted = not self.expand_untrusted
        previous_key = self._selected_key
        self._rebuild_outline()
        index = (
            self._outline_keys.index(previous_key)
            if previous_key in self._outline_keys
            else 0
        )
        self._select_outline(index)
        state = "已展开" if self.expand_untrusted else "已折叠"
        self.notify(f"不可信片段与来源明细{state}", timeout=2)

    def action_toggle_focus(self) -> None:
        detail = self.query_one("#detail", TraceView)
        if self.focused is detail:
            self.query_one("#outline", ListView).focus()
        else:
            detail.focus()

    def action_cursor_down(self) -> None:
        if self.focused is self.query_one("#detail", TraceView):
            self.query_one("#detail", TraceView).action_scroll_down()
        else:
            self.query_one("#outline", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        if self.focused is self.query_one("#detail", TraceView):
            self.query_one("#detail", TraceView).action_scroll_up()
        else:
            self.query_one("#outline", ListView).action_cursor_up()

    def action_interrupt(self) -> None:
        self.interrupted = True
        self._finish(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy":
            self.action_copy_trace()
            return
        self._finish(event.button.id == "approve")


class TextualApprovalPresenter:
    def __init__(self, *, color_policy: str = "auto", input_source=None) -> None:
        """[2026-08-08] input_source 提供 pause/resume 时，TUI 期间独占 stdin，
        避免常驻 InputReader 与 Textual 驱动竞争同一文件描述符。"""
        self.color_policy = color_policy
        self.input_source = input_source
        self.request: PreparedProviderRequest | None = None
        self.payload: MaterializedSendPayload | None = None
        self.estimate: ContextUsageEstimate | None = None
        self.app: PromptApprovalApp | None = None

    async def decide(self, request, payload, estimate, warning) -> ApprovalDecision:
        self.request, self.payload, self.estimate = request, payload, estimate
        self.app = PromptApprovalApp(
            request, payload, estimate, warning=warning, color_policy=self.color_policy
        )
        input_source = self.input_source
        if input_source is not None:
            await input_source.pause()
        try:
            decision = await self.app.run_async()
        finally:
            if input_source is not None:
                await input_source.resume()
        if self.app.interrupted:
            raise TurnInterrupted()
        if decision is None:
            raise DebugPresentationError()
        return decision

    def clear(self) -> None:
        self.request = None
        self.payload = None
        self.estimate = None
        self.app = None
