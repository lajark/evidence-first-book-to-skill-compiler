"""Stable, locator-preserving LLM chunking tests."""

from __future__ import annotations

from book2skill.domain import Locator, LocatorKind, TextBlock
from book2skill.llm.chunking import chunk_blocks


def _block(text: str, paragraph: int) -> TextBlock:
    return TextBlock(
        text=text,
        locator=Locator(kind=LocatorKind.PARAGRAPH, paragraph=paragraph),
    )


def test_chunking_is_stable_and_carries_neighbour_context() -> None:
    entries = [
        (_block("first block " * 20, 1), "source-b1"),
        (_block("second block " * 20, 2), "source-b2"),
    ]

    first = chunk_blocks("source", entries, target_tokens=80, context_tokens=10)
    second = chunk_blocks("source", entries, target_tokens=80, context_tokens=10)

    assert first == second
    assert [chunk.chunk_id for chunk in first] == ["source-chunk-1", "source-chunk-2"]
    assert first[0].items[0].source_block_id == "source-b1"
    assert first[0].items[0].context_after
    assert first[1].items[0].context_before


def test_oversized_block_is_split_without_changing_provenance_block_id() -> None:
    chunks = chunk_blocks(
        "source",
        [(_block("word " * 1000, 1), "source-b1")],
        target_tokens=80,
    )

    items = [item for chunk in chunks for item in chunk.items]
    assert len(items) > 1
    assert {item.source_block_id for item in items} == {"source-b1"}
    assert all(item.input_id.startswith("source-b1-part-") for item in items)
