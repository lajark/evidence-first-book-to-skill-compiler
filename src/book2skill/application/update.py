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
5. On ``confirm=True``, validates provenance and review state, atomically
   publishes the Skill tree, then appends all Schema changes in one atomic
   batch. A Schema commit failure triggers a compensating Skill rollback.

The default (no ``confirm``) only computes and returns the suggestion, so a
user can review the fold-in plan before any file is touched (PRD FR-03-4:
"确认后原子更新").
"""

from __future__ import annotations

import hashlib
import shutil
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from book2skill.application.analyze import AnalyzeUseCase
from book2skill.application.artifacts import (
    load_compilation_artifact,
    verify_compilation_artifact,
)
from book2skill.application.bundle_trace import verify_units_against_raw
from book2skill.application.diff import (
    DiffEngine,
    MergeResult,
    UnitChange,
    bundle_to_units,
)
from book2skill.application.gate import GateError
from book2skill.application.publisher import Publisher, PublishRecord, load_skill_meta
from book2skill.application.update_transaction import (
    UpdateTransaction,
    UpdateTransactionStore,
)
from book2skill.compiler import SkillSpec
from book2skill.domain import (
    ConflictRecord,
    DomainError,
    ErrorCode,
    KnowledgeStatus,
    KnowledgeUnit,
    active_view,
    build_supersession,
)
from book2skill.domain.models import SourceManifest
from book2skill.llm.runtime import AnalysisRunManifest, LLMRuntimeConfig
from book2skill.storage import (
    FileRawStorage,
    KnowledgeSchemaStorage,
    OverrideStorage,
    RawStorage,
    atomic_write,
)

_DEFAULT_SOURCE_VERSION = 1


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
    analysis_errors: list[GateError] = field(default_factory=list)
    review_item_ids: list[str] = field(default_factory=list)
    analysis_conflict_ids: list[str] = field(default_factory=list)
    analysis_run: AnalysisRunManifest | None = None

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
            "review_items": len(self.review_item_ids),
            "analysis_conflicts": len(self.analysis_conflict_ids),
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
        raw_storage: RawStorage | None = None,
        publisher: Publisher | None = None,
        runtime_config: LLMRuntimeConfig | None = None,
    ) -> None:
        self._data_home = data_home.resolve()
        self._analyze = analyze_use_case or AnalyzeUseCase(
            data_home=data_home, runtime_config=runtime_config
        )
        self._schema_storage = schema_storage or KnowledgeSchemaStorage(
            self._data_home
        )
        self._override_storage = override_storage or OverrideStorage(
            self._data_home
        )
        self._raw_storage = raw_storage or FileRawStorage(self._data_home)
        self._publisher = publisher or Publisher(self._data_home)
        self._transactions = UpdateTransactionStore(self._data_home)
        self._recover_pending_transactions()

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
        replace_sources: bool = False,
        _persist_raw: bool = False,
    ) -> UpdateSuggestion:
        """Compute the fold-in plan without writing anything.

        Loads old units from the Schema layer, re-analyses *new_sources*, diffs
        and merges. The default is add-only: units absent from the new-source
        analysis remain active. Set *replace_sources* only when the supplied
        inputs intentionally replace the prior source set and old-only units
        may be deprecated. Raises :class:`DomainError` on missing collection or
        empty analysis.
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
            new_sources,
            collection_id=coll_id,
            rights_note=rights_note,
            persist_raw=_persist_raw,
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
        old_by_id = {unit.unit_id: unit for unit in old_active}
        # Analyze output is provisional. For an existing unit, a generated
        # ``candidate`` status is not semantic drift and must not cause an
        # otherwise-idempotent update on every run.
        new_units = [
            unit.model_copy(
                update={"review_status": old_by_id[unit.unit_id].review_status}
            )
            if unit.unit_id in old_by_id
            and str(unit.review_status) == KnowledgeStatus.CANDIDATE.value
            else unit
            for unit in new_units
        ]
        engine = DiffEngine()
        diff_result = engine.diff(
            old_active,
            new_units,
            allow_removals=replace_sources,
        )
        overrides = self._override_storage.load_active_overrides(coll_id)
        merge_result = engine.merge_with_overrides(
            old_active,
            new_units,
            overrides,
            allow_removals=replace_sources,
        )

        return UpdateSuggestion(
            added=diff_result.added,
            removed=diff_result.removed,
            modified=diff_result.modified,
            conflicts=diff_result.conflicts,
            merge=merge_result,
            collection_id=coll_id,
            new_source_ids=bundle.source_ids,
            analysis_errors=list(analyze_result.errors),
            review_item_ids=sorted(
                item.item_id
                for item in bundle.review_queue
                if item.disposition != "resolved"
            ),
            analysis_conflict_ids=sorted(
                conflict.conflict_id
                for conflict in bundle.conflicts
                if str(conflict.status) == "open"
            ),
            analysis_run=bundle.analysis_run,
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
        replace_sources: bool = False,
    ) -> UpdateResult:
        """Plan and (on confirm) atomically publish the folded-in Skill.

        Source replacement is explicit. A partial analysis is never allowed to
        confirm a plan that would deprecate old units, because absence may only
        reflect a failed input rather than an intentional removal.
        """
        coll_id, resolved_spec = self.resolve_meta(
            skill_dir, collection_id=collection_id, spec=spec
        )
        suggestion = self.plan(
            skill_dir,
            new_sources,
            collection_id=coll_id,
            rights_note=rights_note,
            replace_sources=replace_sources,
            _persist_raw=confirm,
        )

        if not confirm:
            return UpdateResult(
                skill_dir=Path(skill_dir),
                suggestion=suggestion,
                published=False,
                reason="dry_run",
                collection_id=coll_id,
            )

        if suggestion.removed and suggestion.analysis_errors:
            raise DomainError(
                code=ErrorCode.EXTRACT_PARTIAL_FAILURE,
                input_id=coll_id,
                message=(
                    "Cannot confirm source replacement: analysis was partial "
                    f"and would deprecate {len(suggestion.removed)} active "
                    "unit(s)."
                ),
                recovery=(
                    "Resolve every source-analysis error and retry, or use the "
                    "default add-only fold-in mode."
                ),
                details={
                    "analysis_error_count": len(suggestion.analysis_errors),
                    "removed_count": len(suggestion.removed),
                },
            )

        if suggestion.review_item_ids:
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id=coll_id,
                message="Open review items block publication.",
                recovery="Resolve every required review item and retry.",
                details={"review_item_ids": suggestion.review_item_ids},
            )

        preserved_overrides = (
            suggestion.merge.preserved_overrides if suggestion.merge else []
        )
        if preserved_overrides:
            preserved_ids = sorted(
                {override.override_id for override in preserved_overrides}
            )
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id=coll_id,
                message=(
                    "Active human overrides target units that this update "
                    "would remove or replace without a merge base."
                ),
                recovery=(
                    "Review or supersede the listed overrides before "
                    "confirming the update."
                ),
                details={"preserved_override_ids": preserved_ids},
            )

        if not suggestion.has_changes:
            return UpdateResult(
                skill_dir=Path(skill_dir),
                suggestion=suggestion,
                published=False,
                reason="no_changes",
                collection_id=coll_id,
            )

        publish_units = self._approve_confirmed_changes(suggestion)
        unresolved_conflicts = self._unresolved_conflict_ids(suggestion)
        verified_manifests = verify_units_against_raw(
            publish_units,
            self._raw_storage,
            source_version=_DEFAULT_SOURCE_VERSION,
            input_id=coll_id,
        )
        self._check_manifest_override(source_manifests, verified_manifests, coll_id)
        pending_records = self._prepare_unit_changes(suggestion, publish_units)
        transaction = self._transactions.begin(
            coll_id,
            Path(skill_dir),
            pending_records=[
                (unit.unit_id, unit.record_version) for unit in pending_records
            ],
        )
        published = False
        try:
            transaction = self._transactions.update(
                transaction, state="publishing"
            )
            record = self._publisher.publish(
                Path(skill_dir),
                publish_units,
                resolved_spec,
                collection_id=coll_id,
                source_manifests=verified_manifests,
                counts=suggestion.counts(),
                unresolved_conflicts=unresolved_conflicts,
                transaction_id=transaction.transaction_id,
                analysis_run=suggestion.analysis_run,
            )
            published = True
            active_pointer_sha256, publish_index_sha256 = (
                self._publication_resource_hashes(resolved_spec.name)
            )
            transaction = self._transactions.update(
                transaction,
                state="skill_published",
                published_at=record.published_at,
                artifact_id=record.artifact_id,
                publish_id=record.publish_id,
                active_pointer_sha256=active_pointer_sha256,
                publish_index_sha256=publish_index_sha256,
                snapshot_path=(
                    str(record.snapshot_path) if record.snapshot_path else None
                ),
            )
        except Exception:
            # Publisher compensates failures before returning a record. Keep a
            # journal whose state write failed so startup recovery can inspect
            # the transaction rather than assuming it never committed.
            if not published and transaction.state in {"prepared", "publishing"}:
                self._transactions.remove(transaction)
            raise

        try:
            self._schema_storage.save_units_atomic(coll_id, pending_records)
        except Exception as exc:  # noqa: BLE001 - storage transaction boundary
            try:
                self._rollback_transaction(transaction)
            except DomainError as rollback_exc:
                raise rollback_exc from exc
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id=coll_id,
                message="Schema commit failed; the prior Skill was restored.",
                recovery="Resolve the Schema storage error and retry the update.",
                details={"storage_error": type(exc).__name__},
            ) from exc
        transaction = self._transactions.update(
            transaction,
            state="schema_committed",
            schema_sha256=self._schema_hash(coll_id),
        )
        self._transactions.update(transaction, state="committed")
        self._transactions.remove(transaction)
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

    def _recover_pending_transactions(self) -> None:
        """Reconcile incomplete Update journals before accepting new work."""
        for transaction in self._transactions.pending():
            if transaction.state == "committed":
                self._transactions.remove(transaction)
                continue
            if transaction.state == "rollback_required":
                raise DomainError(
                    code=ErrorCode.PUBLISH_ROLLBACK_FAILED,
                    input_id=transaction.collection_id,
                    message="An earlier Update requires manual rollback.",
                    recovery=(
                        "Restore the Skill snapshot and remove the pending "
                        "transaction only after verification."
                    ),
                    details={"transaction_id": transaction.transaction_id},
                )
            if transaction.state == "prepared":
                # No publish call was started; this is a harmless abandoned
                # intent left by a crash before the commit boundary.
                self._transactions.remove(transaction)
                continue
            if transaction.state == "publishing" and not self._transaction_visible(
                transaction
            ):
                # Publisher failed before swapping its staged tree.
                self._transactions.remove(transaction)
                continue
            if self._schema_commit_visible(transaction):
                self._transactions.update(transaction, state="committed")
                self._transactions.remove(transaction)
                continue
            self._rollback_transaction(transaction)

    def _transaction_visible(self, transaction: UpdateTransaction) -> bool:
        meta = load_skill_meta(Path(transaction.skill_dir))
        if meta is None or meta.transaction_id != transaction.transaction_id:
            return False
        if transaction.artifact_id is None:
            artifact_visible = True
        else:
            artifact = load_compilation_artifact(Path(transaction.skill_dir))
            artifact_visible = (
                artifact is not None
                and artifact.artifact_id == transaction.artifact_id
            )
        if not artifact_visible:
            return False
        if transaction.active_pointer_sha256 is not None:
            pointer = (
                self._data_home
                / ".active"
                / f"{Path(transaction.skill_dir).name}.json"
            )
            if not self._hash_matches(pointer, transaction.active_pointer_sha256):
                return False
        return transaction.publish_index_sha256 is None or self._hash_matches(
            self._data_home / "wiki" / "index.md", transaction.publish_index_sha256
        )

    def _schema_commit_visible(self, transaction: UpdateTransaction) -> bool:
        """Check whether the atomic Schema batch landed before a crash."""
        schema_path = self._schema_path(transaction.collection_id)
        if not transaction.pending_records:
            schema_visible = schema_path.exists()
        else:
            try:
                records = self._schema_storage.load_units(transaction.collection_id)
            except OSError:
                return False
            present = {(unit.unit_id, unit.record_version) for unit in records}
            schema_visible = all(
                key in present for key in transaction.pending_records
            )
        if not schema_visible:
            return False
        if transaction.schema_sha256 is not None and not self._hash_matches(
            schema_path, transaction.schema_sha256
        ):
            return False
        if transaction.active_pointer_sha256 is not None:
            pointer = (
                self._data_home
                / ".active"
                / f"{Path(transaction.skill_dir).name}.json"
            )
            if not self._hash_matches(pointer, transaction.active_pointer_sha256):
                return False
        if transaction.publish_index_sha256 is not None and not self._hash_matches(
            self._data_home / "wiki" / "index.md", transaction.publish_index_sha256
        ):
            return False
        if transaction.artifact_id is None:
            return True
        artifact = load_compilation_artifact(Path(transaction.skill_dir))
        if artifact is None or artifact.artifact_id != transaction.artifact_id:
            return False
        return verify_compilation_artifact(Path(transaction.skill_dir), artifact)

    def _publication_resource_hashes(self, skill_name: str) -> tuple[str, str]:
        pointer = self._data_home / ".active" / f"{skill_name}.json"
        index = self._data_home / "wiki" / "index.md"
        return self._file_hash(pointer), self._file_hash(index)

    def _schema_hash(self, collection_id: str) -> str:
        return self._file_hash(self._schema_path(collection_id))

    def _schema_path(self, collection_id: str) -> Path:
        return self._data_home / "schema" / collection_id / "units.jsonl"

    @staticmethod
    def _file_hash(path: Path) -> str:
        if not path.is_file():
            raise FileNotFoundError(path)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def _hash_matches(cls, path: Path, expected: str) -> bool:
        try:
            return cls._file_hash(path) == expected
        except OSError:
            return False

    def _rollback_transaction(self, transaction: UpdateTransaction) -> None:
        """Rollback a journaled publication or mark it unrecoverable."""
        try:
            self._restore_schema_snapshot(transaction)
            skill_dir = Path(transaction.skill_dir)
            if transaction.snapshot_path:
                self._publisher.rollback_latest(skill_dir)
            else:
                meta = load_skill_meta(skill_dir)
                if meta is None or meta.transaction_id != transaction.transaction_id:
                    raise RuntimeError(
                        "journal transaction does not match the current Skill"
                    )
                if skill_dir.exists():
                    shutil.rmtree(skill_dir)
            self._transactions.remove(transaction)
        except Exception as exc:  # noqa: BLE001 - recovery boundary
            with suppress(OSError):
                self._transactions.update(transaction, state="rollback_required")
            raise DomainError(
                code=ErrorCode.PUBLISH_ROLLBACK_FAILED,
                input_id=transaction.collection_id,
                message="An incomplete Update could not be rolled back.",
                recovery="Inspect the Skill and snapshot directories manually.",
                details={
                    "transaction_id": transaction.transaction_id,
                    "rollback_error": type(exc).__name__,
                },
            ) from exc

    def _restore_schema_snapshot(self, transaction: UpdateTransaction) -> None:
        """Restore the pre-update append-only history before clearing a journal."""
        if not transaction.schema_snapshot_path:
            return
        snapshot = Path(transaction.schema_snapshot_path)
        if not snapshot.is_file():
            raise FileNotFoundError(snapshot)
        target = self._schema_path(transaction.collection_id)
        if transaction.schema_existed:
            atomic_write(target, snapshot.read_text(encoding="utf-8"))
        else:
            with suppress(FileNotFoundError):
                target.unlink()

    # ------------------------------------------------------------------
    # Unit persistence (append-only)
    # ------------------------------------------------------------------

    def _prepare_unit_changes(
        self,
        suggestion: UpdateSuggestion,
        publish_units: list[KnowledgeUnit],
    ) -> list[KnowledgeUnit]:
        """Prepare append-only records without mutating the Schema layer.

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
            return []

        added_ids = {u.unit_id for u in suggestion.added}
        modified_by_id = {change.unit_id: change for change in suggestion.modified}
        pending: list[KnowledgeUnit] = []

        for unit in publish_units:
            uid = unit.unit_id
            if uid in added_ids:
                pending.append(unit)
            elif uid in modified_by_id:
                pending.append(build_supersession(modified_by_id[uid].old, unit))

        for old_unit in suggestion.removed:
            deprecated = old_unit.model_copy(
                update={"review_status": KnowledgeStatus.SUPERSEDED}
            )
            pending.append(build_supersession(old_unit, deprecated))
        return pending

    @staticmethod
    def _approve_confirmed_changes(
        suggestion: UpdateSuggestion,
    ) -> list[KnowledgeUnit]:
        """Treat a clean ``confirm`` as explicit approval of changed candidates."""
        merge = suggestion.merge
        if merge is None:
            return []
        changed_ids = {unit.unit_id for unit in suggestion.added}
        changed_ids.update(change.unit_id for change in suggestion.modified)
        approved: list[KnowledgeUnit] = []
        for unit in merge.merged:
            if unit.unit_id in changed_ids and str(unit.review_status) in {
                KnowledgeStatus.CANDIDATE.value,
                KnowledgeStatus.REVIEWED.value,
            }:
                unit = unit.model_copy(
                    update={"review_status": KnowledgeStatus.APPROVED}
                )
            approved.append(unit)
        return approved

    @staticmethod
    def _unresolved_conflict_ids(suggestion: UpdateSuggestion) -> list[str]:
        ids = list(suggestion.analysis_conflict_ids)
        ids.extend(conflict.conflict_id for conflict in suggestion.conflicts)
        if suggestion.merge is not None:
            ids.extend(
                conflict.conflict_id
                for conflict in suggestion.merge.new_conflicts
            )
        return sorted(set(ids))

    @staticmethod
    def _check_manifest_override(
        supplied: list[SourceManifest] | None,
        verified: list[SourceManifest],
        collection_id: str,
    ) -> None:
        """Reject caller-supplied provenance that disagrees with verified Raw."""
        if supplied is None:
            return
        supplied_ids = {
            (item.source_id, item.version, item.content_sha256) for item in supplied
        }
        verified_ids = {
            (item.source_id, item.version, item.content_sha256) for item in verified
        }
        if supplied_ids != verified_ids or len(supplied_ids) != len(supplied):
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id=collection_id,
                message="Supplied source manifests do not match verified Raw records.",
                recovery="Remove the override or pass the exact verified manifests.",
                details={"reason": "source_manifest_mismatch"},
            )


__all__ = [
    "UpdateUseCase",
    "UpdateSuggestion",
    "UpdateResult",
]
