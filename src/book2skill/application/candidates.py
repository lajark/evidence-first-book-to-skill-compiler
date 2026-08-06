"""Shared CandidateUnit → KnowledgeUnit conversion.

Build (FR-03-2/3) and Diff (FR-03-4) both need to turn Analyze-stage
:class:`~book2skill.application.models.CandidateUnit` records into
persisted :class:`~book2skill.domain.KnowledgeUnit` entities.  The
conversion encodes domain policy — unknown ``kind`` falls back to
:data:`~book2skill.domain.UnitKind.TECHNIQUE`, missing source refs get a
synthetic ``unknown`` ref, and the candidate ``review_status`` string is
mapped to the canonical :class:`~book2skill.domain.KnowledgeStatus` enum.

Centralising it here avoids drift between the two use cases (the diff
engine previously hand-copied the build converter).
"""

from __future__ import annotations

from typing import Any

from book2skill.application.models import CandidateUnit
from book2skill.domain import (
    EvidenceLevel,
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    UnitKind,
)

__all__ = [
    "candidate_to_unit",
    "ref_from_dict",
    "map_review_status",
]


def candidate_to_unit(c: CandidateUnit) -> KnowledgeUnit:
    """Convert an Analyze-stage :class:`CandidateUnit` to a persisted unit.

    The candidate is the provisional shape produced by the LLM/mock adapter;
    the persisted unit carries the same content but with typed fields
    (:class:`UnitKind`, :class:`KnowledgeRef`) and the canonical
    ``review_status`` semantics (``candidate`` -> :data:`KnowledgeStatus.CANDIDATE`).

    Unknown ``kind`` values fall back to :data:`UnitKind.TECHNIQUE` so the
    build never crashes on a slightly-off bundle; the discrepancy is
    surfaced by downstream review (review_queue / quality report).
    """
    try:
        kind = UnitKind(c.kind)
    except ValueError:
        kind = UnitKind.TECHNIQUE

    refs = [ref_from_dict(r) for r in c.source_refs]
    if not refs:
        # Defensive: schema guarantees ≥1 ref, but hand-edited bundles may slip.
        refs = [KnowledgeRef(source_id="unknown", block_id="unknown")]

    status = map_review_status(c.review_status)
    return KnowledgeUnit(
        unit_id=c.unit_id,
        kind=kind,
        content=c.content,
        conditions=c.conditions,
        exceptions=c.exceptions,
        source_refs=refs,
        confidence=c.confidence,
        review_status=status,
        record_version=c.record_version,
        evidence_level=EvidenceLevel(c.evidence_level),
        evidence_note=c.evidence_note,
    )


def ref_from_dict(data: dict[str, Any]) -> KnowledgeRef:
    """Build a :class:`KnowledgeRef` from a candidate source_ref dict."""
    return KnowledgeRef(
        source_id=str(data.get("source_id", "unknown")),
        block_id=str(data.get("block_id", "unknown")),
        quote=data.get("quote"),
    )


def map_review_status(value: str) -> KnowledgeStatus:
    """Map the candidate ``review_status`` string to the canonical enum.

    Falls back to :data:`KnowledgeStatus.CANDIDATE` for unknown values so a
    slightly-off bundle never blocks the build.
    """
    mapping = {
        "candidate": KnowledgeStatus.CANDIDATE,
        "reviewed": KnowledgeStatus.REVIEWED,
        "approved": KnowledgeStatus.APPROVED,
        "rejected": KnowledgeStatus.REJECTED,
        "superseded": KnowledgeStatus.SUPERSEDED,
    }
    return mapping.get(value, KnowledgeStatus.CANDIDATE)
