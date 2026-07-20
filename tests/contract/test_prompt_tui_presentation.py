"""[2026-07-20] 颜色仅增强表达；无色、控制字符和数百来源仍有完整文本边界。"""

from bai_agent.debug.tui import SOURCE_PALETTE, resolve_color_enabled, safe_terminal_text


def test_palette_is_stable_and_no_color_preserves_text_semantics() -> None:
    assert SOURCE_PALETTE == {
        "config_file": "cyan",
        "data_file": "green",
        "runtime": "yellow",
        "generated": "magenta",
    }
    assert not resolve_color_enabled("never", environ={}, supports_color=True)
    assert not resolve_color_enabled("auto", environ={"NO_COLOR": "1"}, supports_color=True)
    assert not resolve_color_enabled("auto", environ={}, supports_color=False)
    assert resolve_color_enabled("always", environ={"NO_COLOR": "1"}, supports_color=True)


def test_control_characters_are_visible_not_structural() -> None:
    rendered = safe_terminal_text("正文\x1b[31m伪标签\x00")
    assert "\\x1b" in rendered and "\\x00" in rendered
