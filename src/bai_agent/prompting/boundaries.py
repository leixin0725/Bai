"""[2026-07-21] 统一构造模型可见的不可信数据边界及其精确来源片段。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Iterable

from bai_agent.domain.models import (
    AnnotatedFragment,
    AnnotatedFragmentKind,
    ConfigAsset,
    SourceKind,
    SourceRef,
    TrustLevel,
    content_hash,
)


_BLOCK_NAME = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$")


@dataclass(frozen=True, slots=True)
class PromptTextPiece:
    """[2026-07-21] 未定位的提示片段；定位只在最终拼接时执行一次。"""

    piece_id: str
    content: str
    sources: tuple[SourceRef, ...]
    trust: TrustLevel
    entry_id: str | None = None
    kind: AnnotatedFragmentKind = AnnotatedFragmentKind.BODY


@dataclass(frozen=True, slots=True)
class RenderedPromptText:
    text: str
    fragments: tuple[AnnotatedFragment, ...]


def source_from_asset(asset: ConfigAsset) -> SourceRef:
    """[2026-07-21] 配置来源始终绑定加载快照，而不是重新读取磁盘。"""

    return SourceRef(
        source_kind=SourceKind.CONFIG_FILE,
        source_id=asset.asset_id,
        project_relative_path=f"config/{asset.project_relative_path}",
        content_sha256=asset.content_sha256,
        revision=asset.revision,
        producer="config_loader",
    )


def pieces_from_fragments(fragments: Iterable[AnnotatedFragment]) -> tuple[PromptTextPiece, ...]:
    return tuple(
        PromptTextPiece(
            piece_id=fragment.fragment_id,
            content=fragment.content,
            sources=fragment.sources,
            trust=fragment.trust,
            entry_id=fragment.entry_id,
            kind=fragment.kind,
        )
        for fragment in fragments
    )


def position_pieces(pieces: Iterable[PromptTextPiece]) -> RenderedPromptText:
    """[2026-07-21] 按追加顺序定位，禁止用正文搜索恢复重复片段来源。"""

    text_parts: list[str] = []
    fragments: list[AnnotatedFragment] = []
    cursor = 0
    for piece in pieces:
        if not piece.content:
            continue
        start = cursor
        text_parts.append(piece.content)
        cursor += len(piece.content)
        fragments.append(
            AnnotatedFragment(
                fragment_id=piece.piece_id,
                kind=piece.kind,
                entry_id=piece.entry_id or piece.piece_id,
                start=start,
                end=cursor,
                content=piece.content,
                sources=piece.sources,
                trust=piece.trust,
            )
        )
    return RenderedPromptText(text="".join(text_parts), fragments=tuple(fragments))


class UntrustedBoundaryRenderer:
    """[2026-07-21] 每个逻辑块只生成一对内容绑定边界。"""

    def __init__(self, boundary_asset: ConfigAsset) -> None:
        self.asset = boundary_asset
        self.config_source = source_from_asset(boundary_asset)

    @property
    def instruction_text(self) -> str:
        return self.asset.content.strip()

    @staticmethod
    def _validate_block_name(block_name: str) -> None:
        if not _BLOCK_NAME.fullmatch(block_name):
            raise ValueError("不可信边界 block 名称无效")

    @classmethod
    def boundary_id(cls, block_name: str, content: str) -> str:
        cls._validate_block_name(block_name)
        return sha256(f"{block_name}\0{content}".encode("utf-8")).hexdigest()[:8]

    def _frame_sources(self, block_name: str, boundary_id: str, frame_text: str) -> tuple[SourceRef, ...]:
        generated = SourceRef(
            source_kind=SourceKind.GENERATED,
            source_id=f"generated:untrusted-boundary:{block_name}:{boundary_id}",
            content_sha256=content_hash(frame_text),
            revision=self.asset.revision,
            entity_ids=(block_name, boundary_id),
            producer="untrusted_boundary_renderer",
        )
        return self.config_source, generated

    def wrap(self, block_name: str, pieces: Iterable[PromptTextPiece]) -> RenderedPromptText:
        inner = tuple(pieces)
        content = "".join(piece.content for piece in inner)
        boundary_id = self.boundary_id(block_name, content)
        opening = f"[UNTRUSTED {block_name}#{boundary_id}]\n"
        before_close = "" if content.endswith("\n") else "\n"
        closing = f"[/UNTRUSTED {block_name}#{boundary_id}]"
        frame_sources = self._frame_sources(block_name, boundary_id, opening + before_close + closing)
        framed = (
            PromptTextPiece(
                piece_id=f"{block_name}:untrusted-boundary-open",
                content=opening,
                sources=frame_sources,
                trust=TrustLevel.TRUSTED_INSTRUCTION,
                entry_id=block_name,
                kind=AnnotatedFragmentKind.SEPARATOR,
            ),
            *inner,
            PromptTextPiece(
                piece_id=f"{block_name}:untrusted-boundary-close-separator",
                content=before_close,
                sources=frame_sources,
                trust=TrustLevel.TRUSTED_INSTRUCTION,
                entry_id=block_name,
                kind=AnnotatedFragmentKind.SEPARATOR,
            ),
            PromptTextPiece(
                piece_id=f"{block_name}:untrusted-boundary-close",
                content=closing,
                sources=frame_sources,
                trust=TrustLevel.TRUSTED_INSTRUCTION,
                entry_id=block_name,
                kind=AnnotatedFragmentKind.SEPARATOR,
            ),
        )
        return position_pieces(framed)

    def rendered_length(self, block_name: str, content: str) -> int:
        """[2026-07-21] 选择器可在不伪造来源的情况下精确预留边界字符。"""

        boundary_id = self.boundary_id(block_name, content)
        opening = f"[UNTRUSTED {block_name}#{boundary_id}]\n"
        before_close = "" if content.endswith("\n") else "\n"
        closing = f"[/UNTRUSTED {block_name}#{boundary_id}]"
        return len(opening) + len(content) + len(before_close) + len(closing)

    def compose_system_instruction(
        self,
        persona_text: str,
        persona_source: SourceRef,
        *,
        composition_id: str,
    ) -> RenderedPromptText:
        """[2026-07-21] 人格与边界说明同消息发送，但保持各自独立来源区间。"""

        persona = persona_text.rstrip()
        boundary = self.instruction_text
        separator_source = SourceRef(
            source_kind=SourceKind.GENERATED,
            source_id=f"generated:trusted-composition:{composition_id}",
            content_sha256=content_hash("\n\n"),
            revision=self.asset.revision,
            entity_ids=(composition_id,),
            producer="trusted_prompt_composer",
        )
        return position_pieces(
            (
                PromptTextPiece(
                    piece_id=f"{composition_id}:persona",
                    content=persona,
                    sources=(persona_source,),
                    trust=TrustLevel.TRUSTED_INSTRUCTION,
                    entry_id=composition_id,
                ),
                PromptTextPiece(
                    piece_id=f"{composition_id}:separator",
                    content="\n\n",
                    sources=(separator_source,),
                    trust=TrustLevel.TRUSTED_INSTRUCTION,
                    entry_id=composition_id,
                    kind=AnnotatedFragmentKind.SEPARATOR,
                ),
                PromptTextPiece(
                    piece_id=f"{composition_id}:untrusted-boundary-instruction",
                    content=boundary,
                    sources=(self.config_source,),
                    trust=TrustLevel.TRUSTED_INSTRUCTION,
                    entry_id=composition_id,
                ),
            )
        )


__all__ = [
    "PromptTextPiece",
    "RenderedPromptText",
    "UntrustedBoundaryRenderer",
    "pieces_from_fragments",
    "position_pieces",
    "source_from_asset",
]
