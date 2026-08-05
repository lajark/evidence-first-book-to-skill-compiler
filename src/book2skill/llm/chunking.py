"""Deterministic, locator-preserving chunks for analysis requests."""

from __future__ import annotations

from dataclasses import dataclass

from book2skill.domain import Locator, TextBlock


@dataclass(frozen=True)
class ChunkItem:
    """One analyzable fragment, mapped back to an immutable source block."""

    input_id: str
    source_block_id: str
    text: str
    locator: Locator
    context_before: str = ""
    context_after: str = ""


@dataclass(frozen=True)
class AnalysisChunk:
    """A bounded request containing one or more ordered source fragments."""

    source_id: str
    chunk_id: str
    items: tuple[ChunkItem, ...]


def chunk_blocks(
    source_id: str,
    entries: list[tuple[TextBlock, str]],
    *,
    target_tokens: int = 1200,
    context_tokens: int = 100,
) -> list[AnalysisChunk]:
    """Split ordered blocks deterministically without losing source mapping.

    A single oversized extractor block is split on paragraph/newline boundaries.
    Every fragment retains the original block ID and locator; ``input_id`` is
    unique only within this analysis run and is never accepted as provenance.
    """
    if target_tokens <= 0 or context_tokens < 0:
        raise ValueError(
            "target_tokens must be positive and context_tokens non-negative"
        )
    items: list[ChunkItem] = []
    for block, block_id in entries:
        fragments = _split_text(block.text, target_tokens)
        for index, text in enumerate(fragments, start=1):
            suffix = f"-part-{index}" if len(fragments) > 1 else ""
            items.append(
                ChunkItem(
                    input_id=f"{block_id}{suffix}",
                    source_block_id=block_id,
                    text=text,
                    locator=block.locator,
                )
            )
    contextual = _with_context(items, context_tokens)
    chunks: list[AnalysisChunk] = []
    current: list[ChunkItem] = []
    current_tokens = 0
    for item in contextual:
        tokens = estimate_tokens(item.text)
        if current and current_tokens + tokens > target_tokens:
            chunks.append(
                AnalysisChunk(
                    source_id,
                    f"{source_id}-chunk-{len(chunks) + 1}",
                    tuple(current),
                )
            )
            current = []
            current_tokens = 0
        current.append(item)
        current_tokens += tokens
    if current:
        chunks.append(
            AnalysisChunk(
                source_id,
                f"{source_id}-chunk-{len(chunks) + 1}",
                tuple(current),
            )
        )
    return chunks


def _split_text(text: str, target_tokens: int) -> list[str]:
    if estimate_tokens(text) <= target_tokens:
        return [text]
    lines = [line for line in text.splitlines(keepends=True) if line] or [text]
    fragments: list[str] = []
    current = ""
    for line in lines:
        if estimate_tokens(line) > target_tokens:
            if current:
                fragments.append(current)
                current = ""
            fragments.extend(_split_long_line(line, target_tokens))
        elif current and estimate_tokens(current + line) > target_tokens:
            fragments.append(current)
            current = line
        else:
            current += line
    if current:
        fragments.append(current)
    return fragments


def _split_long_line(text: str, target_tokens: int) -> list[str]:
    # Character windows are only a final fallback after retaining every newline
    # boundary; estimate_tokens remains the authority for the resulting limit.
    pieces: list[str] = []
    remaining = text
    while remaining:
        end = min(len(remaining), max(1, target_tokens * 3))
        while end > 1 and estimate_tokens(remaining[:end]) > target_tokens:
            end -= 1
        pieces.append(remaining[:end])
        remaining = remaining[end:]
    return pieces


def _with_context(items: list[ChunkItem], context_tokens: int) -> list[ChunkItem]:
    return [
        ChunkItem(
            input_id=item.input_id,
            source_block_id=item.source_block_id,
            text=item.text,
            locator=item.locator,
            context_before=_tail(items[index - 1].text, context_tokens)
            if index
            else "",
            context_after=_head(items[index + 1].text, context_tokens)
            if index + 1 < len(items)
            else "",
        )
        for index, item in enumerate(items)
    ]


def _tail(text: str, budget: int) -> str:
    start = max(0, len(text) - budget * 4)
    return text[start:]


def _head(text: str, budget: int) -> str:
    return text[: budget * 4]


def estimate_tokens(text: str) -> int:
    """Use the same conservative Latin/CJK heuristic without compiler imports."""
    cjk = sum(1 for char in text if _is_cjk(char))
    other = len(text) - cjk
    return (cjk + 1) // 2 + (other + 3) // 4


def _is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x4E00 <= codepoint <= 0x9FFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


__all__ = ["AnalysisChunk", "ChunkItem", "chunk_blocks"]
