"""Update / Fold-in use case (TASK-015, PRD FR-03-4).

Update is the fourth build mode. Given an already published Skill directory
and new source material, it:

1. Loads the *old* knowledge units from the Schema layer
   (``<data_home>/schema/<collection_id>/units.jsonl``).
2. Re-analyses the *new* sources via
   :class:`~book2skill.application.analyze.AnalyzeUseCase`
   and converts the resulting candidates to knowledge units
   (:func:`~book2skill.application.diff.bundle_to_units`).
3. Diffs old vs new (:meth:`~book2skill.application.diff.DiffEngine.diff`) and
   three-way-merges with human overrides
   (:meth:`~book2skill.application.diff.DiffEngine.merge_with_overrides`).
4. Produces an :class:`UpdateSuggestion` (added / modified / deprecated /
   conflicts + merge outcome). This is the default, read-only, no writes.
5. On ``confirm=True`` persists the unit changes (append-only: new/modified
   units appended, removed units superseded with ``review_status=superseded``)
   and asks :class:`~book2skill.application.publisher.Publisher` to atomically
   re-publish the Skill tree with a rollback snapshot.

The default (no ``confirm``) only computes and returns the suggestion, so a
user can review the fold-in plan before any file is touched (PRD FR-03-4:
"确认后原子更新").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from book2skill.application.analyze import AnalyzeUseCase
from book2skill.application.diff import (
    DiffEngine,
    MergeResult,
    UnitChange,
    bundle_to_units,
)
from book2skill.application.publisher import Publisher, PublishRecord, load_skill_meta
from book2skill.compiler import SkillSpec
from book2skill.domain import (
    ConflictRecord,
    DomainError,
    ErrorCode,
    KnowledgeStatus,
    KnowledgeUnit,
    active_view,
)
from book2skill.domain.models import SourceManifest
from book2skill.storage import KnowledgeSchemaStorage, OverrideStorage


@dataclass
class UpdateSuggestion:
    """Read-only fold-in plan: what changes if the new sources are folded in."""

    added: list[KnowledgeUnit] = field(default_factory=list)
    removed: list[KnowledgeUnit] = field(default_factory=list)
    modified: list[UnitChange] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    merge: MergeResult | None = None
    collection_id: str = ""
    new_source_ids: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified or self.conflicts)

    def counts(self) -> dict[str, int]:
        return {
            "added": len(self.added),
            "modified": len(self.modified),
            "deprecated": len(self.removed),
            "conflicts": len(self.conflicts),
            "new_conflicts": len(self.merge.new_conflicts) if self.merge else 0,
        }


@dataclass
class UpdateResult:
    """Outcome of an Update run."""

    skill_dir: Path
    suggestion: UpdateSuggestion
    published: bool = False
    reason: str | None = None
    publish_record: PublishRecord | None = None
    collection_id: str = ""


class UpdateUseCase:
    """Orchestrate the Update / Fold-in mode.

    Dependencies are injected so tests can substitute fakes. *data_home* is
    required for Update (PRD FR-03-4 + design decision): the old units live in
    the Schema layer under it.
    """

    def __init__(
        self,
        data_home: Path,
        *,
        analyze_use_case: AnalyzeUseCase | None = None,
        schema_storage: KnowledgeSchemaStorage | None = None,
        override_storage: OverrideStorage | None = None,
        publisher: Publisher | None = None,
    ) -> None:
        self._data_home = data_home.resolve()
        self._analyze = analyze_use_case or AnalyzeUseCase(data_home=data_home)
        self._schema_storage = schema_storage or KnowledgeSchemaStorage(
            self._data_home
        )
        self._override_storage = override_storage or OverrideStorage(
            self._data_home
        )
        self._publisher = publisher or Publisher(self._data_home)

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_meta(
        skill_dir: Path,
        *,
        collection_id: str | None = None,
        spec: SkillSpec | None = None,
    ) -> tuple[str, SkillSpec]:
        """Resolve (collection_id, spec) for an update target.

        Priority: explicit ``collection_id`` / ``spec`` args override the
        values persisted in ``skill_dir/skill.meta.json``. When no meta file
        exists (a Skill produced before this task), both must be supplied
        explicitly; otherwise :data:`ErrorCode.DIFF_INPUT_INVALID` is raised.
        """
        skill_dir = Path(skill_dir)
        meta = load_skill_meta(skill_dir)
        resolved_coll = collection_id or (meta.collection_id if meta else None)
        resolved_spec = spec or (meta.spec if meta else None)
        if resolved_coll is None or resolved_spec is None:
            missing: list[str] = []
            if resolved_coll is None:
                missing.append("--collection-id")
            if resolved_spec is None:
                missing.append(
                    "--name/--description/--use-when (or build via "
                    "`book2skill build` which writes skill.meta.json)"
                )
            raise DomainError(
                code=ErrorCode.DIFF_INPUT_INVALID,
                input_id=str(skill_dir),
                message=(
                    f"Cannot resolve collection_id/spec for '{skill_dir}': "
                    f"no skill.meta.json and missing {missing}."
                ),
                recovery=(
                    "Rebuild with `book2skill build` (which writes "
                    "skill.meta.json), or pass --collection-id and the "
                    "--name/--description/--use-when SkillSpec flags."
                ),
            )
        return resolved_coll, resolved_spec

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        skill_dir: Path,
        new_sources: list[str],
        *,
        collection_id: str | None = None,
        rights_note: str | None = None,
    ) -> UpdateSuggestion:
        """Compute the fold-in plan without writing anything.

        Loads old units from the Schema layer, re-analyses *new_sources*, diffs
        and merges. Raises :class:`DomainError` on missing collection or empty
        analysis.
        """
        coll_id, _spec = self.resolve_meta(skill_dir, collection_id=collection_id)

        old_units = self._schema_storage.load_units(coll_id)
        if not old_units:
            raise DomainError(
                code=ErrorCode.DIFF_INPUT_INVALID,
                input_id=coll_id,
                message=(
                    f"Collection '{coll_id}' has no persisted units; nothing "
                    "to update against."
                ),
                recovery=(
                    "Run `book2skill build` with --data-home first to "
                    "populate the Schema layer."
                ),
            )

        # Active old view: latest record per unit_id, excluding already-
        # deprecated (superseded) or dropped (rejected) records. Without this
        # filter a re-run would re-flag already-superseded units as removed,
        # breaking idempotency (FR-10).
        old_active = active_view(old_units)
        if not old_active:
            raise DomainError(
                code=ErrorCode.DIFF_INPUT_INVALID,
                input_id=coll_id,
                message=(
                    f"Collection '{coll_id}' has no active units (all "
                    "superseded/rejected); nothing to update against."
                ),
                recovery="Add fresh sources or rebuild the collection.",
            )

        analyze_result = self._analyze.execute(
            new_sources, collection_id=coll_id, rights_note=rights_note
        )
        bundle = analyze_result.bundle
        if bundle is None:
            raise DomainError(
                code=ErrorCode.BUILD_INPUT_INVALID,
                input_id=coll_id,
                message="New sources yielded no analysis bundle.",
                recovery="Check the source paths and formats; see gate errors.",
            )

        new_units = bundle_to_units(bundle)
        engine = DiffEngine()
        diff_result = engine.diff(old_active, new_units)
        overrides = self._override_storage.load_active_overrides(coll_id)
        merge_result = engine.merge_with_overrides(old_active, new_units, overrides)

        return UpdateSuggestion(
            added=diff_result.added,
            removed=diff_result.removed,
            modified=diff_result.modified,
            conflicts=diff_result.conflicts,
            merge=merge_result,
            collection_id=coll_id,
            new_source_ids=bundle.source_ids,
        )

    def execute(
        self,
        skill_dir: Path,
        new_sources: list[str],
        spec: SkillSpec | None = None,
        *,
        collection_id: str | None = None,
        rights_note: str | None = None,
        confirm: bool = False,
        source_manifests: list[SourceManifest] | None = None,
    ) -> UpdateResult:
        """Plan and (on confirm) atomically publish the folded-in Skill."""
        coll_id, resolved_spec = self.resolve_meta(
            skill_dir, collection_id=collection_id, spec=spec
        )
        suggestion = self.plan(
            skill_dir,
            new_sources,
            collection_id=coll_id,
            rights_note=rights_note,
        )

        if not confirm:
            return UpdateResult(
                skill_dir=Path(skill_dir),
                suggestion=suggestion,
                published=False,
                reason="dry_run",
                collection_id=coll_id,
            )

        if not suggestion.has_changes:
            return UpdateResult(
                skill_dir=Path(skill_dir),
                suggestion=suggestion,
                published=False,
                reason="no_changes",
                collection_id=coll_id,
            )

        self._persist_unit_changes(coll_id, suggestion)
        record = self._publisher.publish(
            Path(skill_dir),
            suggestion.merge.merged if suggestion.merge else [],
            resolved_spec,
            collection_id=coll_id,
            source_manifests=source_manifests,
            counts=suggestion.counts(),
        )
        return UpdateResult(
            skill_dir=Path(skill_dir),
            suggestion=suggestion,
            published=True,
            publish_record=record,
            collection_id=coll_id,
        )

    def rollback(self, skill_dir: Path) -> PublishRecord:
        """Restore the latest published snapshot of *skill_dir*."""
        return self._publisher.rollback_latest(Path(skill_dir))

    # ------------------------------------------------------------------
    # Unit persistence (append-only)
    # ------------------------------------------------------------------

    def _persist_unit_changes(
        self, collection_id: str, suggestion: UpdateSuggestion
    ) -> None:
        """Append new/modified/superseded unit records to the Schema layer.

        Append-only invariant is preserved:

        - *added* units (in new, not old) are appended fresh.
        - *modified* units (in both, drifted) supersede the old record with the
          override-reconciled value via bumped ``record_version``.
        - *unchanged* units are skipped — they already exist in the Schema
          layer and re-appending would create duplicate version records.
        - *removed* units (in old, gone from new) are superseded with
          ``review_status=superseded`` so the deprecation is auditable
          history, never an in-place erasure.
        """
        merge = suggestion.merge
        if merge is None:
            return

        added_ids = {u.unit_id for u in suggestion.added}
        modified_ids = {c.unit_id for c in suggestion.modified}

        for unit in merge.merged:
            uid = unit.unit_id
            if uid in added_ids:
                # Fresh unit; append with its own record_version (typically 1).
                self._schema_storage.save_unit(collection_id, unit)
            elif uid in modified_ids:
                # Drifted unit; supersede the old record (bumps version, sets
                # supersedes link) with the override-reconciled value.
                self._schema_storage.supersede_unit(collection_id, uid, unit)
            # else unchanged: skip to avoid duplicate version records.

        for old_unit in suggestion.removed:
            deprecated = old_unit.model_copy(
                update={"review_status": KnowledgeStatus.SUPERSEDED}
            )
            self._schema_storage.supersede_unit(
                collection_id, old_unit.unit_id, deprecated
            )


__all__ = [
    "UpdateUseCase",
    "UpdateSuggestion",
    "UpdateResult",
]
