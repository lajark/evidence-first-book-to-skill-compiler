"""Canonical knowledge-layer domain models and operations.

These are the persistent, reviewable counterparts of the Analyze-stage
:class:`~book2skill.application.models.CandidateUnit`. A ``KnowledgeUnit`` is
the normalized, versioned entity that survives human review and is persisted
to the Schema layer (``units.jsonl``). ``ConflictRecord`` and ``ReviewItem``
live here as the single source of truth and are re-exported by the application
layer to avoid divergence.

The pure functions in this module (:func:`cluster_units`,
:func:`detect_conflicts`, :func:`build_supersession`) encode domain rules with
no I/O dependency so they can be unit-tested in isolation. Clustering and
conflict detection use conservative rule-based heuristics; an LLM-assisted
upgrade is a P1 concern and is called out in comments.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from book2skill.domain.state import ConflictStatus, KnowledgeStatus

#: Jaccard token-overlap above which two units of the same kind are considered
#: synonymous and clustered together. Tuned for rule-based M3; revisit when an
#: LLM drives clustering (P1).
_CLUSTER_THRESHOLD = 0.5

#: Words that, when shared between two ``principle`` units, suggest a direct
#: contradiction worth flagging as an open conflict (no auto-resolution).
_NEGATION_MARKERS = ("not", "never", "avoid", "do not", "don't", "must not")


class UnitKind(StrEnum):
    """Kind of a knowledge unit.

    Values mirror the ``kind`` enum in ``schemas/knowledge-unit.schema.json``.
    """

    FRAMEWORK = "framework"
    PRINCIPLE = "principle"
    TECHNIQUE = "technique"
    ANTI_PATTERN = "anti_pattern"
    TERM = "term"
    CASE = "case"
    CHECKLIST = "checklist"
    DECISION_RULE = "decision_rule"


class KnowledgeRef(BaseModel):
    """A pointer from a knowledge unit back to its source block.

    Corresponds to an item in ``KnowledgeUnit.source_refs``. ``quote`` is the
    optional verbatim excerpt justifying the unit; it may be ``None`` when the
    locator alone is sufficient.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    block_id: str
    quote: str | None = None


class KnowledgeUnit(BaseModel):
    """A normalized, versioned knowledge entity.

    Conforms to ``schemas/knowledge-unit.schema.json``. ``record_version`` and
    ``supersedes`` encode append-only history: a correction never mutates an
    existing record in place, it appends a new one (see
    :func:`build_supersession`).
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    unit_id: str
    kind: UnitKind
    content: str = Field(..., min_length=1)
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    source_refs: list[KnowledgeRef] = Field(..., min_length=1)
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    review_status: KnowledgeStatus
    record_version: int = Field(1, ge=1)
    supersedes: str | None = None


class ConflictRecord(BaseModel):
    """A pair of conflicting knowledge units, kept side by side.

    The domain record is a superset of the M1
    :class:`~book2skill.application.models.ConflictRecord` used in the analysis
    bundle: it adds the optional ``conditions`` (applicable preconditions) so
    reviewers can record *when* each view holds without merging them.
    Conflicts are flagged ``open`` and never auto-resolved.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    conflict_id: str
    unit_ids: list[str] = Field(..., min_length=2)
    description: str
    status: ConflictStatus
    conditions: list[str] = Field(default_factory=list)


class ReviewItem(BaseModel):
    """A human-review task pointing at a knowledge unit, structure or conflict.

    Superset of the M1 analysis-bundle ``ReviewItem``: ``disposition`` and
    ``reviewer`` capture the review workflow outcome and accountability, which
    the analysis stage does not yet populate.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    ref_type: Literal["candidate_unit", "structure", "conflict"]
    ref_id: str
    reason: str
    severity: Literal["info", "warning", "error"]
    disposition: Literal["open", "resolved", "deferred"] | None = None
    reviewer: str | None = None


class KnowledgeCluster(BaseModel):
    """A group of synonymous units sharing a kind and content overlap.

    Units are clustered for navigation/deduplication only: their original
    content is preserved verbatim so each source's specific expression remains
    auditable (PRD: "同义方法可聚类但保留来源特有表达").
    """

    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    unit_ids: list[str] = Field(..., min_length=1)
    rationale: str


# ---------------------------------------------------------------------------
# Pure domain operations (no I/O)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Lowercase the text into an alpha-numeric token set.

    Stop-words are intentionally not removed: the rule-based heuristic favours
    precision over recall and stays deterministic. An LLM-assisted clusterer
    (P1) would replace this entirely.
    """
    return {tok for tok in re.findall(r"[a-z0-9]+", text.lower()) if tok}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Token-set Jaccard similarity, 0.0 for two empty sets."""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_units(units: list[KnowledgeUnit]) -> list[KnowledgeCluster]:
    """Group synonymous units without merging their content.

    Two units cluster together when they share a ``kind`` and their normalized
    content token sets overlap above :data:`_CLUSTER_THRESHOLD`. A unit with no
    close neighbor forms its own singleton cluster. Every returned cluster
    references the original unit ids verbatim; no content is altered.
    """
    clusters: list[KnowledgeCluster] = []
    assigned: set[int] = set()
    tokens = [_tokenize(u.content) for u in units]

    for i, unit in enumerate(units):
        if i in assigned:
            continue
        members = [i]
        assigned.add(i)
        for j in range(i + 1, len(units)):
            if j in assigned:
                continue
            other = units[j]
            if other.kind != unit.kind:
                continue
            if _jaccard(tokens[i], tokens[j]) >= _CLUSTER_THRESHOLD:
                members.append(j)
                assigned.add(j)
        clusters.append(
            KnowledgeCluster(
                cluster_id=f"cluster-{len(clusters)}",
                unit_ids=[units[m].unit_id for m in members],
                rationale=(
                    f"Same kind '{unit.kind}' with >= {_CLUSTER_THRESHOLD} "
                    "content overlap."
                ),
            )
        )
    return clusters


def detect_conflicts(units: list[KnowledgeUnit]) -> list[ConflictRecord]:
    """Flag conflicting views as open conflicts, without arbitrating.

    Pairs are scanned directly (not via :func:`cluster_units`, which only
    groups same-``kind`` synonyms) because a tension typically crosses kinds,
    e.g. a ``principle`` versus its ``anti_pattern``. Two heuristics apply:

    - A ``principle`` and an ``anti_pattern`` whose content overlaps (same
      topic) are treated as a direct tension.
    - Two ``principle`` units sharing topic overlap but diverging via negation
      markers are flagged as a contradiction.

    Every conflict is emitted with ``status="open"``; resolution is a human
    decision (PRD: "冲突观点并列，不自动裁决").
    """
    conflicts: list[ConflictRecord] = []
    for a in range(len(units)):
        for b in range(a + 1, len(units)):
            ua, ub = units[a], units[b]
            if _is_principle_anti_pattern_tension(ua, ub) or _is_negation_pair(
                ua, ub
            ):
                conflicts.append(
                    ConflictRecord(
                        conflict_id=f"conflict-{len(conflicts)}",
                        unit_ids=[ua.unit_id, ub.unit_id],
                        description=(
                            f"Conflicting views between '{ua.unit_id}' "
                            f"and '{ub.unit_id}'; presented without "
                            "arbitration."
                        ),
                        status=ConflictStatus.OPEN,
                    )
                )
    return conflicts


def _is_principle_anti_pattern_tension(
    a: KnowledgeUnit, b: KnowledgeUnit
) -> bool:
    """True when a principle and an anti_pattern discuss the same topic."""
    kinds = {a.kind, b.kind}
    if {UnitKind.PRINCIPLE, UnitKind.ANTI_PATTERN} != kinds:
        return False
    return _jaccard(_tokenize(a.content), _tokenize(b.content)) >= 0.34


def _is_negation_pair(a: KnowledgeUnit, b: KnowledgeUnit) -> bool:
    """True when two principles contradict via shared negation markers.

    Both must be principles, at least one must carry a negation marker, and
    their token sets must overlap (same topic) yet not be identical
    (genuinely divergent wording).
    """
    if a.kind != UnitKind.PRINCIPLE or b.kind != UnitKind.PRINCIPLE:
        return False
    ta, tb = _tokenize(a.content), _tokenize(b.content)
    if ta == tb:
        return False
    if not (ta & tb):
        return False
    return any(m in a.content.lower() for m in _NEGATION_MARKERS) or any(
        m in b.content.lower() for m in _NEGATION_MARKERS
    )


def latest_record(
    records: list[KnowledgeUnit], unit_id: str
) -> KnowledgeUnit | None:
    """Return the highest ``record_version`` record for *unit_id*.

    Under the append-only Schema layer, the latest record is the "current"
    state of a unit; older records remain for history.
    """
    matching = [r for r in records if r.unit_id == unit_id]
    if not matching:
        return None
    return max(matching, key=lambda r: r.record_version)


def build_supersession(
    old: KnowledgeUnit, new_unit: KnowledgeUnit
) -> KnowledgeUnit:
    """Build a new record that supersedes *old* without mutating it.

    The returned unit keeps *old*'s ``unit_id``, bumps ``record_version`` and
    points back via ``supersedes``. Neither *old* nor *new_unit* is modified;
    callers persist the result to append to history.
    """
    return new_unit.model_copy(
        update={
            "unit_id": old.unit_id,
            "record_version": old.record_version + 1,
            "supersedes": old.unit_id,
        }
    )


#: Review statuses excluded from the active view — already-deprecated or
#: explicitly dropped units should not re-surface on a re-run.
_INACTIVE_STATUSES: frozenset[str] = frozenset({"superseded", "rejected"})


def current_view(units: list[KnowledgeUnit]) -> list[KnowledgeUnit]:
    """Return the latest record per ``unit_id``, preserving first-seen order.

    Applies :func:`latest_record` semantics across the whole list so
    superseded history does not skew comparisons. Dicts preserve insertion
    order (Python 3.7+), so updating a value for an existing key does not
    move it — ``list(seen.values())`` is already ordered by first appearance.
    """
    seen: dict[str, KnowledgeUnit] = {}
    for u in units:
        current = seen.get(u.unit_id)
        if current is None or u.record_version > current.record_version:
            seen[u.unit_id] = u
    return list(seen.values())


def active_view(units: list[KnowledgeUnit]) -> list[KnowledgeUnit]:
    """Latest record per ``unit_id`` that is not superseded or rejected.

    Wraps :func:`current_view` and drops inactive records so a re-run is
    idempotent — an already-deprecated unit is not re-flagged as removed.
    """
    return [
        u
        for u in current_view(units)
        if str(u.review_status) not in _INACTIVE_STATUSES
    ]


__all__ = [
    "UnitKind",
    "KnowledgeRef",
    "KnowledgeUnit",
    "ConflictRecord",
    "ReviewItem",
    "KnowledgeCluster",
    "cluster_units",
    "detect_conflicts",
    "latest_record",
    "build_supersession",
    "current_view",
    "active_view",
]
