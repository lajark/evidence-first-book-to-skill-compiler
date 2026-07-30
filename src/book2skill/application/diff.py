"""Diff engine and three-way merge for the Schema layer (TASK-014, PRD FR-03-4).

Compares two :class:`~book2skill.domain.KnowledgeUnit` collections and produces
a structured :class:`DiffResult` (added / removed / modified / conflicts /
unchanged). The merge step applies human-authored :class:`Override` records on
top of a re-extracted collection using field-level three-way merge semantics,
preserving human edits while pulling in generator changes that do not conflict.

Design notes
------------

- :class:`DiffEngine` is a pure engine: it takes in-memory unit lists and
  returns in-memory result objects. No I/O. The CLI / Update use case
  (TASK-015) is responsible for loading collections and persisting merged
  results.
- Field-level three-way merge compares ``base`` (old generator output),
  ``ours`` (human override) and ``theirs`` (new generator output) per field.
  When both ``ours`` and ``theirs`` modified the same field, the change is
  flagged as an unresolvable conflict (PRD: "冲突观点并列，不自动裁决"); the
  human value wins as canonical and the generator alternative is recorded in
  ``new_conflicts`` for review.
- Conflicts across collections reuse :func:`~book2skill.domain.detect_conflicts`
  so the same heuristics (principle/anti-pattern tension, negation pairs)
  apply to inter-version drift as to intra-collection analysis.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from book2skill.application.candidates import candidate_to_unit
from book2skill.application.models import AnalysisBundle
from book2skill.domain import (
    ConflictRecord,
    KnowledgeUnit,
    current_view,
    detect_conflicts,
)

if TYPE_CHECKING:
    from book2skill.storage.schema_storage import KnowledgeSchemaStorage


# ---------------------------------------------------------------------------
# Override model
# ---------------------------------------------------------------------------


class OverrideField(StrEnum):
    """KnowledgeUnit fields that may be overridden by a human editor.

    The set is intentionally narrow: structural identity (``unit_id`` /
    ``kind`` / ``source_refs``) is not overridable — those changes should go
    through the :func:`~book2skill.domain.build_supersession` flow instead.
    """

    CONTENT = "content"
    CONDITIONS = "conditions"
    EXCEPTIONS = "exceptions"
    CONFIDENCE = "confidence"
    REVIEW_STATUS = "review_status"


class Override(BaseModel):
    """A field-level human edit, stored separately from generated content.

    Conforms to PRD FR-06: "人工编辑区使用结构化 override/patch 记录". Each
    override targets one field of one unit; multiple overrides on the same
    (unit_id, field) form an append-only history where only the latest
    non-superseded record is active.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    override_id: str
    unit_id: str
    field: OverrideField
    value: Any
    reason: str = Field(..., min_length=1)
    reviewer: str = "human"
    created_at: _dt.datetime
    superseded: bool = False

    def validate_value(self) -> None:
        """Check that *value* matches the type expected for :attr:`field`.

        Raises :class:`ValueError` on mismatch. Called by
        :class:`~book2skill.storage.OverrideStorage` before persisting so bad
        overrides never reach disk.
        """
        f = self.field
        if not isinstance(f, OverrideField):
            f = OverrideField(self.field)
        if f is OverrideField.CONTENT:
            if not isinstance(self.value, str) or not self.value:
                raise ValueError("content override must be a non-empty str")
        elif f in (OverrideField.CONDITIONS, OverrideField.EXCEPTIONS):
            if not isinstance(self.value, list) or not all(
                isinstance(x, str) for x in self.value
            ):
                raise ValueError(f"{f} override must be a list[str]")
        elif f is OverrideField.CONFIDENCE:
            if not isinstance(self.value, (int, float)) or not 0.0 <= self.value <= 1.0:
                raise ValueError("confidence override must be a float in [0, 1]")
        elif f is OverrideField.REVIEW_STATUS:
            allowed = {"candidate", "reviewed", "approved", "rejected", "superseded"}
            if self.value not in allowed:
                raise ValueError(
                    f"review_status override must be one of {sorted(allowed)}"
                )


# ---------------------------------------------------------------------------
# Diff / merge result models
# ---------------------------------------------------------------------------


@dataclass
class UnitChange:
    """A same-unit content drift between old and new collections."""

    unit_id: str
    old: KnowledgeUnit
    new: KnowledgeUnit
    changed_fields: list[str]


@dataclass
class DiffResult:
    """Structured outcome of comparing two unit collections."""

    added: list[KnowledgeUnit] = field(default_factory=list)
    removed: list[KnowledgeUnit] = field(default_factory=list)
    modified: list[UnitChange] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    unchanged: list[KnowledgeUnit] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified or self.conflicts)


@dataclass
class MergeConflict:
    """A field-level merge conflict on a single unit.

    Unlike :class:`~book2skill.domain.ConflictRecord` (which tracks tensions
    between two different units), a merge conflict arises when the human
    override and the generator re-extract both modified the same field of
    the same unit differently. The human value wins as canonical; the
    generator alternative is preserved here for reviewer action.
    """

    conflict_id: str
    unit_id: str
    field: str
    human_value: Any
    generator_value: Any
    description: str


@dataclass
class MergeResult:
    """Outcome of a three-way merge with human overrides.

    ``merged`` holds the reconciled unit set (override values win over
    generator values on non-conflicting fields). ``new_conflicts`` collects
    same-field double-edits that could not be auto-merged; ``unresolvable``
    lists the underlying :class:`UnitChange` records for reviewer context.
    """

    merged: list[KnowledgeUnit] = field(default_factory=list)
    applied_overrides: list[Override] = field(default_factory=list)
    preserved_overrides: list[Override] = field(default_factory=list)
    new_conflicts: list[MergeConflict] = field(default_factory=list)
    unresolvable: list[UnitChange] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------


#: Fields compared when detecting modification. ``record_version`` and
#: ``supersedes`` are history metadata and excluded; ``source_refs`` changes
#: are reported as a content drift since refs identify the evidence chain.
_DIFFED_FIELDS: tuple[str, ...] = (
    "kind",
    "content",
    "conditions",
    "exceptions",
    "confidence",
    "review_status",
    "source_refs",
)


class DiffEngine:
    """Pure diff and three-way merge engine over KnowledgeUnit collections."""

    def diff(
        self,
        old: list[KnowledgeUnit],
        new: list[KnowledgeUnit],
    ) -> DiffResult:
        """Compare two unit collections by ``unit_id``.

        Both sides are normalised via :func:`~book2skill.domain.latest_record`
        so superseded history does not skew the diff. Units present only in
        ``old`` become ``removed``; only in ``new`` become ``added``; in both
        with field drift become ``modified``; identical become ``unchanged``.
        Cross-collection conflicts are detected with
        :func:`~book2skill.domain.detect_conflicts` over the union of both
        current views.
        """
        old_current = current_view(old)
        new_current = current_view(new)
        old_by_id = {u.unit_id: u for u in old_current}
        new_by_id = {u.unit_id: u for u in new_current}

        added: list[KnowledgeUnit] = []
        removed: list[KnowledgeUnit] = []
        modified: list[UnitChange] = []
        unchanged: list[KnowledgeUnit] = []

        for uid, new_u in new_by_id.items():
            old_u = old_by_id.get(uid)
            if old_u is None:
                added.append(new_u)
                continue
            changed = _changed_fields(old_u, new_u)
            if changed:
                modified.append(
                    UnitChange(
                        unit_id=uid, old=old_u, new=new_u, changed_fields=changed
                    )
                )
            else:
                unchanged.append(new_u)

        for uid, old_u in old_by_id.items():
            if uid not in new_by_id:
                removed.append(old_u)

        # Cross-collection conflict scan over the union of current views.
        union = list(old_by_id.values()) + [
            u for u in new_current if u.unit_id not in old_by_id
        ]
        # Deduplicate by (unit_id, record_version) to avoid scanning the same
        # record twice when it appears in both sides unchanged.
        seen: set[tuple[str, int]] = set()
        union_dedup: list[KnowledgeUnit] = []
        for u in union:
            key = (u.unit_id, u.record_version)
            if key in seen:
                continue
            seen.add(key)
            union_dedup.append(u)
        conflicts = detect_conflicts(union_dedup)

        return DiffResult(
            added=added,
            removed=removed,
            modified=modified,
            conflicts=conflicts,
            unchanged=unchanged,
        )

    def merge_with_overrides(
        self,
        base: list[KnowledgeUnit],
        new: list[KnowledgeUnit],
        overrides: list[Override],
    ) -> MergeResult:
        """Three-way field-level merge (PRD FR-06, ARCHITECTURE §4).

        Per field of each unit:

        - If ``ours`` (override) and ``theirs`` (new) both differ from
          ``base`` on the same field → unresolvable conflict: ``ours`` wins
          as canonical, ``theirs`` recorded in ``new_conflicts``.
        - If only ``ours`` changed → keep ``ours``.
        - If only ``theirs`` changed → adopt ``theirs``.
        - If neither changed → keep ``base``.

        Units only in ``new`` (added) are passed through unchanged. Units
        only in ``base`` (removed) are dropped from ``merged``; their
        overrides move to ``preserved_overrides`` for reviewer action.
        """
        base_current = current_view(base)
        new_current = current_view(new)
        base_by_id = {u.unit_id: u for u in base_current}
        new_by_id = {u.unit_id: u for u in new_current}

        active_overrides = _active_overrides(overrides)
        overrides_by_unit: dict[str, list[Override]] = {}
        for ov in active_overrides:
            overrides_by_unit.setdefault(ov.unit_id, []).append(ov)

        merged: list[KnowledgeUnit] = []
        applied: list[Override] = []
        preserved: list[Override] = []
        new_conflicts: list[MergeConflict] = []
        unresolvable: list[UnitChange] = []
        conflict_counter = 0

        for uid, new_u in new_by_id.items():
            base_u = base_by_id.get(uid)
            unit_overrides = overrides_by_unit.get(uid, [])

            if base_u is None:
                # Added unit: no base to merge against; adopt new as-is.
                merged.append(new_u)
                # Overrides for newly-added units have no base to compare;
                # record them as preserved for reviewer awareness.
                preserved.extend(unit_overrides)
                continue

            merged_u, unit_applied, unit_conflicts, unit_unresolvable = (
                _merge_unit(base_u, new_u, unit_overrides, uid, conflict_counter)
            )
            merged.append(merged_u)
            applied.extend(unit_applied)
            new_conflicts.extend(unit_conflicts)
            unresolvable.extend(unit_unresolvable)
            conflict_counter += len(unit_conflicts)

        # Overrides for removed units (in base but not in new) are preserved.
        for uid in base_by_id:
            if uid in new_by_id:
                continue
            preserved.extend(overrides_by_unit.get(uid, []))

        return MergeResult(
            merged=merged,
            applied_overrides=applied,
            preserved_overrides=preserved,
            new_conflicts=new_conflicts,
            unresolvable=unresolvable,
        )


# ---------------------------------------------------------------------------
# Bundle → KnowledgeUnit conversion (for diff from AnalysisBundle files)
# ---------------------------------------------------------------------------


def bundle_to_units(bundle: AnalysisBundle) -> list[KnowledgeUnit]:
    """Convert an :class:`AnalysisBundle`'s candidate units to KnowledgeUnits.

    Delegates to the shared :func:`candidate_to_unit` converter so the diff
    CLI stays in lockstep with the build path — no hand-copied logic that can
    drift.
    """
    return [candidate_to_unit(c) for c in bundle.candidate_units]


def load_diff_input(
    spec: str,
    *,
    schema_storage: KnowledgeSchemaStorage | None = None,
) -> list[KnowledgeUnit]:
    """Resolve a diff input spec to a list of KnowledgeUnits.

    *spec* is either:

    - A path to an existing :class:`AnalysisBundle` JSON file, or
    - A collection_id to load from *schema_storage*.

    Raises :class:`ValueError` (caller wraps as :class:`DomainError`) on
    invalid input.
    """
    from pathlib import Path

    from book2skill.domain.errors import DomainError, ErrorCode

    candidate = Path(spec)
    if candidate.is_file():
        try:
            raw = candidate.read_text(encoding="utf-8")
            import json

            data = json.loads(raw)
            bundle = AnalysisBundle.model_validate(data)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise DomainError(
                code=ErrorCode.DIFF_INPUT_INVALID,
                input_id=spec,
                message=f"Invalid AnalysisBundle JSON: {exc}",
                recovery=(
                    "Regenerate the bundle with `book2skill analyze --json`."
                ),
            ) from exc
        if not bundle.candidate_units:
            raise DomainError(
                code=ErrorCode.DIFF_INPUT_INVALID,
                input_id=spec,
                message="AnalysisBundle has no candidate_units; nothing to diff.",
                recovery="Re-run analyze on a richer source.",
            )
        return bundle_to_units(bundle)

    if schema_storage is None:
        raise DomainError(
            code=ErrorCode.DIFF_INPUT_INVALID,
            input_id=spec,
            message=(
                f"'{spec}' is not a file and no --data-home was provided; "
                "cannot resolve as collection_id."
            ),
            recovery="Pass --data-home <dir> or a bundle.json file path.",
        )

    units = schema_storage.load_units(spec)
    if not units:
        raise DomainError(
            code=ErrorCode.DIFF_INPUT_INVALID,
            input_id=spec,
            message=f"Collection '{spec}' has no units or does not exist.",
            recovery=(
                "Check the collection_id, or run `book2skill build` first to "
                "populate the Schema layer."
            ),
        )
    return units


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _changed_fields(old: KnowledgeUnit, new: KnowledgeUnit) -> list[str]:
    """Return the list of diffed fields that differ between *old* and *new*."""
    changed: list[str] = []
    for f in _DIFFED_FIELDS:
        old_val = getattr(old, f)
        new_val = getattr(new, f)
        if f == "confidence" and _confidence_equal(old_val, new_val):
            continue
        if old_val != new_val:
            changed.append(f)
    return changed


def _confidence_equal(a: float | None, b: float | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) < 1e-9


def _active_overrides(overrides: list[Override]) -> list[Override]:
    """Return non-superseded overrides, keeping only the latest per (unit, field)."""
    active = [o for o in overrides if not o.superseded]
    latest: dict[tuple[str, str], Override] = {}
    for o in active:
        key = (o.unit_id, _field_value(o.field))
        existing = latest.get(key)
        if existing is None or o.created_at > existing.created_at:
            latest[key] = o
    return list(latest.values())


def _field_value(field: Any) -> str:
    """Normalise OverrideField enum / string to its string value."""
    if isinstance(field, OverrideField):
        return field.value
    return str(field)


def _merge_unit(
    base_u: KnowledgeUnit,
    new_u: KnowledgeUnit,
    unit_overrides: list[Override],
    unit_id: str,
    conflict_offset: int,
) -> tuple[KnowledgeUnit, list[Override], list[MergeConflict], list[UnitChange]]:
    """Merge one unit across base / new / overrides.

    Returns (merged_unit, applied_overrides, new_conflicts, unresolvable).
    """
    overrides_by_field: dict[str, Override] = {
        _field_value(o.field): o for o in unit_overrides
    }

    merged_data = base_u.model_dump()
    applied: list[Override] = []
    new_conflicts: list[MergeConflict] = []
    unresolvable: list[UnitChange] = []
    conflict_counter = conflict_offset

    for f in _DIFFED_FIELDS:
        base_val = getattr(base_u, f)
        new_val = getattr(new_u, f)
        override = overrides_by_field.get(f)

        if override is None:
            # No human override on this field: adopt generator change.
            if f == "confidence" and _confidence_equal(base_val, new_val):
                continue
            if base_val != new_val:
                merged_data[f] = new_val
            continue

        # Has override on this field.
        ours_val = override.value
        if _values_equal(base_val, ours_val):
            # Human did not actually change this field vs base: adopt theirs.
            if base_val != new_val:
                merged_data[f] = new_val
            applied.append(override)
            continue

        if _values_equal(ours_val, new_val):
            # Human and generator agree: no conflict.
            merged_data[f] = ours_val
            applied.append(override)
            continue

        if _values_equal(base_val, new_val):
            # Generator did not change this field: keep ours.
            merged_data[f] = ours_val
            applied.append(override)
            continue

        # Both sides changed the same field differently → unresolvable.
        # Human value wins as canonical; generator alternative recorded.
        merged_data[f] = ours_val
        applied.append(override)
        new_conflicts.append(
            MergeConflict(
                conflict_id=f"merge-conflict-{conflict_counter}",
                unit_id=unit_id,
                field=f,
                human_value=ours_val,
                generator_value=new_val,
                description=(
                    f"Field '{f}' on unit '{unit_id}' was edited by both "
                    f"human (override) and generator (re-extract); human "
                    "value kept as canonical, generator alternative recorded."
                ),
            )
        )
        conflict_counter += 1

    # Detect overall content drift for the unresolvable list (informational).
    changed = _changed_fields(base_u, new_u)
    has_override_on_changed = any(
        _field_value(o.field) in changed for o in unit_overrides
    )
    if changed and has_override_on_changed and new_conflicts:
        unresolvable.append(
            UnitChange(
                unit_id=unit_id, old=base_u, new=new_u, changed_fields=changed
            )
        )

    merged_u = KnowledgeUnit.model_validate(merged_data)
    return merged_u, applied, new_conflicts, unresolvable


def _values_equal(a: Any, b: Any) -> bool:
    """Loose equality for override value comparison.

    Confidence floats compare with tolerance; lists compare as sets when they
    contain strings (conditions/exceptions order is not significant); other
    values use ``==``.
    """
    if isinstance(a, float) and isinstance(b, float):
        return _confidence_equal(a, b)
    if isinstance(a, list) and isinstance(b, list) and a and isinstance(a[0], str):
        return set(a) == set(b)
    return bool(a == b)


__all__ = [
    "Override",
    "OverrideField",
    "UnitChange",
    "DiffResult",
    "MergeConflict",
    "MergeResult",
    "DiffEngine",
    "bundle_to_units",
    "load_diff_input",
]
