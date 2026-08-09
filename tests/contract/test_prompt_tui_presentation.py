"""[2026-07-21] TUI 颜色、空白与来源折叠只增强展示，不改变可复制审计文本。"""

from bai_agent.debug.tui import (
    MESSAGE_PALETTE,
    SOURCE_PALETTE,
    color_for_part,
    escaped_whitespace,
    is_boundary_frame_part,
    message_colors,
    record_ordinals_for_parts,
    resolve_color_enabled,
    safe_terminal_text,
)
from bai_agent.domain.models import Participation, RequestPart, SourceKind, SourceRef, TrustLevel


def _part(part_id: str, order: int, pointer: str, content: str) -> RequestPart:
    return RequestPart(
        part_id=part_id,
        order=order,
        participation=Participation.INCLUDED,
        trust=TrustLevel.UNTRUSTED_DATA,
        payload_pointer=pointer,
        text_span=(order, order + len(content)),
        content=content,
        sources=(
            SourceRef(
                source_kind=SourceKind.RUNTIME,
                source_id=f"source-{order}",
                entity_ids=(f"entity-{order}",),
                producer="test",
            ),
        ),
    )


def test_palette_is_muted_stable_and_no_color_preserves_text_semantics() -> None:
    assert SOURCE_PALETTE == {
        "config_file": "#91a3ad",
        "data_file": "#9baa96",
        "runtime": "#b2a18d",
        "generated": "#a89caf",
    }
    assert len(MESSAGE_PALETTE) >= 6
    assert message_colors(0) != message_colors(1)
    assert message_colors(1) != message_colors(2)
    assert message_colors(0) == message_colors(len(MESSAGE_PALETTE))
    assert all(
        message_colors(index) != message_colors(index + 1)
        for index in range(len(MESSAGE_PALETTE) * 3)
    )
    assert not resolve_color_enabled("never", environ={}, supports_color=True)
    assert not resolve_color_enabled("auto", environ={"NO_COLOR": "1"}, supports_color=True)
    assert not resolve_color_enabled("auto", environ={}, supports_color=False)
    assert resolve_color_enabled("always", environ={"NO_COLOR": "1"}, supports_color=True)


def test_boundary_frame_part_ids_are_recognized_exactly() -> None:
    def part(part_id: str) -> RequestPart:
        return _part(part_id, 0, "/messages/0/content", "x")

    assert is_boundary_frame_part(part("message:0:recent_records:untrusted-boundary-open"))
    assert is_boundary_frame_part(part("message:0:recent_records:untrusted-boundary-close"))
    assert is_boundary_frame_part(part("message:0:recent_records:untrusted-boundary-close-separator"))
    assert not is_boundary_frame_part(part("message:0:chat-system:untrusted-boundary-instruction"))
    assert not is_boundary_frame_part(part("message:0:chat-system:persona"))
    assert not is_boundary_frame_part(part("message:0:rec-00000000-0000-4000-8000-000000000001:marker"))
    assert not is_boundary_frame_part(part("message:0:recent_records:body"))


def test_record_fragments_alternate_by_first_seen_order_and_keep_one_record_color() -> None:
    first = "rec-00000000-0000-4000-8000-000000000001"
    second = "rec-00000000-0000-4000-8000-000000000002"
    parts = (
        _part(f"message:4:{first}:marker", 0, "/messages/4/content", "a"),
        _part(f"message:4:{first}:marker-separator", 1, "/messages/4/content", "\n"),
        _part(f"message:4:{first}:body", 2, "/messages/4/content", "b"),
        _part(f"message:4:{second}:entry-separator", 3, "/messages/4/content", "\n"),
        _part(f"message:4:{second}:body", 4, "/messages/4/content", "c"),
    )
    ordinals = record_ordinals_for_parts(parts)
    first_colors = {color_for_part(part, ordinals) for part in parts[:3]}
    second_colors = {color_for_part(part, ordinals) for part in parts[3:]}
    assert first_colors == {message_colors(4)[0]}
    assert second_colors == {message_colors(4)[1]}


def test_expanded_whitespace_form_is_deterministic_and_lossless() -> None:
    assert escaped_whitespace("\n\t \u3000") == "\\n\\t\\x20\\u3000"


def test_control_characters_are_visible_not_structural() -> None:
    rendered = safe_terminal_text("正文\x1b[31m伪标签\x00")
    assert "\\x1b" in rendered and "\\x00" in rendered


def test_control_escaping_matches_legacy_per_character_behavior() -> None:
    """[2026-08-08] 正则实现必须与逐字符旧实现逐字节等价，防止转义集合漂移。"""

    def legacy(value: str) -> str:
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

    corpus = "".join(chr(code) for code in range(256)) + "\u3000🙂"
    assert safe_terminal_text(corpus) == legacy(corpus)
