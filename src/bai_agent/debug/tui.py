"""[2026-07-20] Textual 批准界面短生命周期展示完整载荷，并在发送前释放正文来源。"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.widgets import Button, Footer, Static

from bai_agent.domain.errors import DebugPresentationError, TurnInterrupted
from bai_agent.domain.models import (
    ApprovalDecision,
    ContextUsageEstimate,
    MaterializedSendPayload,
    PreparedProviderRequest,
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
    output: list[str] = []
    for character in value:
        code = ord(character)
        if character in {"\n", "\t"}:
            output.append(character)
        elif code < 32 or code == 127:
            output.append(f"\\x{code:02x}")
        else:
            output.append(character)
    return "".join(output)


class PromptApprovalApp(App[ApprovalDecision]):
    CSS = """
    Screen { layout: vertical; }
    #identity, #usage, #warning { height: auto; padding: 0 1; }
    #trace-scroll { height: 1fr; border: solid $accent; }
    #trace { width: 100%; height: auto; padding: 0 1; }
    #actions { height: 3; align: center middle; }
    Button { margin: 0 2; }
    """
    BINDINGS = [
        Binding("a", "approve", "批准并发送", priority=True),
        Binding("c", "copy_trace", "复制框内全部内容", priority=True),
        Binding("w", "toggle_whitespace", "展开/折叠空白片段", priority=True),
        Binding("r", "reject", "拒绝并撤销整轮", priority=True),
        Binding("escape", "reject", "拒绝", priority=True),
        Binding("ctrl+c", "interrupt", "拒绝并退出", priority=True),
    ]

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
        self.expand_whitespace = False

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
        with ScrollableContainer(id="trace-scroll"):
            yield Static(self._trace_renderable(), id="trace", markup=False)
        with Horizontal(id="actions"):
            yield Button("批准并发送 [A]", id="approve", variant="success", disabled=True)
            yield Button("复制框内全部内容 [C]", id="copy")
            yield Button("拒绝并撤销整轮 [R]", id="reject", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.call_after_refresh(self._mark_display_ready)

    def _mark_display_ready(self) -> None:
        self.display_ready = True
        self.query_one("#approve", Button).disabled = False

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

    def _trace_renderable(self, *, expand_whitespace: bool | None = None) -> Text:
        assert self.request is not None and self.payload is not None and self.estimate is not None
        expanded = self.expand_whitespace if expand_whitespace is None else expand_whitespace
        color_enabled = resolve_color_enabled(
            self.color_policy,
            environ=os.environ,
            supports_color=getattr(self.console, "color_system", None) is not None,
        )
        header_style = "bold" if color_enabled else None
        rendered = Text()
        rendered.append("[最终 provider 载荷]\n", style=header_style)
        rendered.append(safe_terminal_text(canonical_json(thaw_json(self.payload.sdk_kwargs))))
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
        record_ordinals = record_ordinals_for_parts(self.request.parts)
        for part in self.request.parts:
            whitespace_only = bool(part.content) and part.content.isspace()
            # [2026-07-21] 折叠模式隐藏整个空白 part（含来源）；展开/复制仍保留完整审计块。
            if whitespace_only and not expanded:
                continue
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
        rendered.append("\n\n[上下文分段估算]\n", style=header_style)
        rendered.append(self._usage_details_text())
        return rendered

    def _trace_text(self) -> str:
        """[2026-07-21] 复制始终展开空白为可逆转义，不携带 Rich 颜色。"""

        return self._trace_renderable(expand_whitespace=True).plain

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
        trace = self.query_one("#trace", Static)
        trace.update("")
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

    def action_toggle_whitespace(self) -> None:
        if self.request is None or self.payload is None:
            return
        self.expand_whitespace = not self.expand_whitespace
        self.query_one("#trace", Static).update(self._trace_renderable())
        state = "已展开" if self.expand_whitespace else "已折叠"
        self.notify(f"空白片段{state}", timeout=2)

    def action_interrupt(self) -> None:
        self.interrupted = True
        self._finish(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy":
            self.action_copy_trace()
            return
        self._finish(event.button.id == "approve")


class TextualApprovalPresenter:
    def __init__(self, *, color_policy: str = "auto") -> None:
        self.color_policy = color_policy
        self.request: PreparedProviderRequest | None = None
        self.payload: MaterializedSendPayload | None = None
        self.estimate: ContextUsageEstimate | None = None
        self.app: PromptApprovalApp | None = None

    async def decide(self, request, payload, estimate, warning) -> ApprovalDecision:
        self.request, self.payload, self.estimate = request, payload, estimate
        self.app = PromptApprovalApp(
            request, payload, estimate, warning=warning, color_policy=self.color_policy
        )
        decision = await self.app.run_async()
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
