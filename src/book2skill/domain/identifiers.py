"""Deterministic identifiers owned by the Core pipeline."""

from __future__ import annotations

from book2skill.domain.models import Locator

_BLOCK_PREFIXES = {
    "page": "pg",
    "chapter": "c",
    "paragraph": "p",
    "table": "t",
    "sheet": "s",
    "unknown": "b",
}


def derive_block_id(source_id: str, locator: Locator, ordinal: int) -> str:
    """Return a stable source-scoped ID for an extracted block.

    The ordinal is the block's stable extraction order. Locator kind is used
    only as a readable prefix; models and source text never get to choose the
    identifier.
    """
    if ordinal < 1:
        raise ValueError("block ordinal must be at least 1")
    prefix = _BLOCK_PREFIXES.get(str(locator.kind), "b")
    return f"{source_id}-{prefix}{ordinal}"


def derive_candidate_unit_id(block_id: str, ordinal: int) -> str:
    """Return a stable ID for the *ordinal* candidate derived from a block."""
    if ordinal < 1:
        raise ValueError("candidate ordinal must be at least 1")
    return f"cu-{block_id}-{ordinal}"


__all__ = ["derive_block_id", "derive_candidate_unit_id"]
