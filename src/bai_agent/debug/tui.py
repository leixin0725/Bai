"""[2026-07-20] Textual 批准界面短生命周期展示完整载荷，并在发送前释放正文来源。"""

from __future__ import annotations

import os
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
    "config_file": "cyan",
    "data_file": "green",
    "runtime": "yellow",
    "generated": "magenta",
}


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
        details = ", ".join(f"{part_id}≈{tokens}" for part_id, tokens in self.estimate.part_tokens.items()) or "无参与片段"
        main = max(self.estimate.part_tokens.items(), key=lambda item: item[1])[0] if self.estimate.part_tokens else "协议开销"
        return (
            f"上下文：输入≈{self.estimate.estimated_input_tokens} + 输出预留{self.estimate.max_output_tokens} "
            f"= 峰值{self.estimate.projected_peak_tokens} / 容量{capacity} ({percent}) "
            f"剩余={remaining} [{self.estimate.risk}]\n"
            f"分段：{details}；协议开销≈{self.estimate.protocol_overhead_tokens}；主要输入={main}"
        )

    def _trace_renderable(self) -> Text:
        assert self.request is not None and self.payload is not None
        rendered = Text()
        rendered.append("[最终 provider 载荷]\n", style="bold")
        rendered.append(safe_terminal_text(canonical_json(thaw_json(self.payload.sdk_kwargs))))
        color_enabled = resolve_color_enabled(
            self.color_policy,
            environ=os.environ,
            supports_color=getattr(self.console, "color_system", None) is not None,
        )
        for part in self.request.parts:
            rendered.append(
                f"\n\n[{part.part_id}] [{part.participation.value}] [{part.trust.value}] 来源={len(part.sources)}\n",
                style="bold",
            )
            rendered.append(safe_terminal_text(part.content))
            for source in part.sources:
                location = source.project_relative_path or "runtime"
                rendered.append(
                    f"\n  [{source.source_kind.value}] 来源={location} producer={source.producer} "
                    f"ids={','.join(source.entity_ids) or '-'}",
                    style=(SOURCE_PALETTE[source.source_kind.value] if color_enabled else None),
                )
            if part.exclusion_reason:
                rendered.append(f"\n  原因 {part.exclusion_reason}")
        return rendered

    def _trace_text(self) -> str:
        return self._trace_renderable().plain

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
