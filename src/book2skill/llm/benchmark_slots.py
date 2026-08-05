"""Benchmark-slot coverage for offline quality-gap detection (OPT-P1-11).

Each evaluation rubric in ``Skill提炼质量评估基准/`` schedules a set of items
(``A1`` … ``F5``) plus scenario cases. OPT-P1-11 turns those items into
machine-readable :class:`BenchmarkSlot` objects: a slot is covered when at
least ``min_count`` candidate units of an allowed ``kind`` contain any of the
slot's ``keywords`` in their content and carry at least one source ref.

This is deliberately a *heuristic* proxy, not a substitute for the rubric's
100-point evaluation. It only answers "does the Analyze output contain any
evidence for this benchmark item at all", which is enough to drive the
``benchmark_gap`` reason in :class:`~book2skill.llm.quality.QualityGate`
without spending evaluator tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from book2skill.application.models import CandidateUnit

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BenchmarkSlot:
    """One rubric item expected to be covered by the Analyze output."""

    slot_id: str
    rubric_item_id: str
    kinds: list[str]
    keywords: list[str]
    min_count: int = 1


def load_benchmark_slots(path: Path) -> list[BenchmarkSlot]:
    """Load and validate a benchmark-slot YAML file.

    Raises :class:`ValueError` for a missing file, an unsupported schema
    version, or a malformed slot entry.
    """
    if not path.exists():
        raise ValueError(f"benchmark slots file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"benchmark slots file must map to a mapping: {path}")
    if data.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError(
            f"unsupported benchmark slots schema_version "
            f"{data.get('schema_version')!r} (expected {_SCHEMA_VERSION})"
        )
    raw_slots = data.get("slots")
    if not isinstance(raw_slots, list) or not raw_slots:
        raise ValueError(
            f"benchmark slots file must define a non-empty 'slots' list: {path}"
        )
    slots: list[BenchmarkSlot] = []
    for entry in raw_slots:
        if not isinstance(entry, dict):
            raise ValueError(f"benchmark slot entry must be a mapping: {entry!r}")
        slots.append(_parse_slot(entry))
    return slots


def _parse_slot(entry: dict[str, Any]) -> BenchmarkSlot:
    slot_id = entry.get("slot_id")
    rubric_item_id = entry.get("rubric_item_id")
    if not isinstance(slot_id, str) or not slot_id:
        raise ValueError("benchmark slot missing non-empty 'slot_id'")
    if not isinstance(rubric_item_id, str) or not rubric_item_id:
        raise ValueError(f"benchmark slot {slot_id!r} missing 'rubric_item_id'")
    kinds = entry.get("kinds")
    keywords = entry.get("keywords")
    if not isinstance(kinds, list) or not kinds or not all(
        isinstance(k, str) for k in kinds
    ):
        raise ValueError(f"benchmark slot {slot_id!r} needs a non-empty 'kinds' list")
    if not isinstance(keywords, list) or not keywords or not all(
        isinstance(k, str) and k for k in keywords
    ):
        raise ValueError(
            f"benchmark slot {slot_id!r} needs a non-empty 'keywords' list"
        )
    min_count = entry.get("min_count", 1)
    if not isinstance(min_count, int) or min_count < 1:
        raise ValueError(
            f"benchmark slot {slot_id!r} 'min_count' must be a positive integer"
        )
    return BenchmarkSlot(
        slot_id=slot_id,
        rubric_item_id=rubric_item_id,
        kinds=list(kinds),
        keywords=list(keywords),
        min_count=min_count,
    )


def compute_slot_coverage(
    candidates: list[CandidateUnit],
    slots: list[BenchmarkSlot],
) -> dict[str, int]:
    """Return ``{slot_id: matched_candidate_count}`` for ``slots``.

    A candidate covers a slot when its ``kind`` is allowed, its content
    contains at least one keyword (case-insensitive), and it carries at least
    one source ref. Each candidate counts at most once per slot.
    """
    coverage: dict[str, int] = {slot.slot_id: 0 for slot in slots}
    for slot in slots:
        allowed = set(slot.kinds)
        keywords = [kw.lower() for kw in slot.keywords]
        for candidate in candidates:
            if not candidate.source_refs:
                continue
            if candidate.kind not in allowed:
                continue
            lowered = candidate.content.lower()
            if any(kw in lowered for kw in keywords):
                coverage[slot.slot_id] += 1
    return coverage


def missing_slots(
    coverage: dict[str, int],
    slots: list[BenchmarkSlot],
) -> list[str]:
    """Return slot ids whose coverage is below their ``min_count``."""
    return [
        slot.slot_id
        for slot in slots
        if coverage.get(slot.slot_id, 0) < slot.min_count
    ]


__all__ = [
    "BenchmarkSlot",
    "compute_slot_coverage",
    "load_benchmark_slots",
    "missing_slots",
]
