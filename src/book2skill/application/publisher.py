"""Atomic publishing for compiled Skills (TASK-015, PRD FR-03-4 / FR-10).

The :class:`Publisher` is the "Publish" stage of the pipeline
(ARCHITECTURE §3 step 9). It takes a freshly compiled set of knowledge units
plus a human-authored :class:`~book2skill.compiler.SkillSpec` and produces the
canonical Skill directory **atomically**: the new tree is staged in a temporary
directory, the previously published tree is moved aside as a snapshot, and the
staged tree is swapped into place. On any failure after the snapshot the
previous version is restored, so a published Skill is never left half-written
(FR-10: "失败保留中间诊断但不污染已发布版本").

Side artefacts produced here (all under ``<data_home>/``):

- ``wiki/index.md`` — a derived registry of published skills, regenerated from
  the append-only log on every publish/rollback (no dual-write divergence).
- ``wiki/publish-log.jsonl`` — one JSON record per publish/rollback, append-only.
- ``snapshots/<skill_name>/<ts>/`` — rolled-back Skill trees, restorable.
- ``.transactions/publish/*.json`` — durable directory-swap journals for
  startup crash recovery.
- ``.active/<skill_name>.json`` — the active version pointer for the published
  tree.
- ``.staging/`` — transient compile scratch, cleaned on every run.
- ``<skill_dir>/skill.meta.json`` — per-skill metadata (collection_id + spec +
  publish_status + timestamps) so :class:`~book2skill.application.update.UpdateUseCase`
  can locate the old collection and reuse the authored shape without re-asking.

Design notes
------------

- Directory-level atomicity relies on ``os.replace`` renaming a non-empty
  directory to a *non-existent* target, which works on both Windows and POSIX.
  The invariant is "the swap target is always absent at swap time": the old
  tree is moved to a snapshot first, freeing the slot for the staged tree.
  Staging, snapshots and the published tree live under the same ``data_home``
  so renames never cross devices.
- ``index.md`` is a *derived* view of ``publish-log.jsonl`` (latest entry per
  skill), mirroring how ``frameworks.jsonl`` is derived from ``units.jsonl`` in
  :mod:`book2skill.storage.schema_storage`. This avoids two sources of truth.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from book2skill.application.artifacts import (
    load_compilation_artifact,
    verify_compilation_artifact,
    write_compilation_artifact,
)
from book2skill.compiler import IRBuilder, SkillSpec, SkillWriter, WikiGenerator
from book2skill.compiler.token_budget import TokenBudget
from book2skill.domain import (
    DomainError,
    ErrorCode,
    KnowledgeStatus,
    KnowledgeUnit,
    PublishStatus,
    SourceManifest,
)
from book2skill.llm.runtime import AnalysisRunManifest
from book2skill.storage import atomic_write
from book2skill.storage.errors import StorageNotFoundError
from book2skill.validation import (
    QualityReport,
    QualityReportWriter,
    ReportStatus,
    Validator,
    evaluate_publication_quality,
)


def _now() -> _dt.datetime:
    """Current UTC time. Overridable via :class:`Publisher`'s ``now`` param."""
    return _dt.datetime.now(_dt.UTC)


def _ts(dt: _dt.datetime) -> str:
    """Sortable, collision-resistant directory/time stamp."""
    return dt.strftime("%Y%m%dT%H%M%S%f")


def _iso(dt: _dt.datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _snap_str(path: Path | None) -> str | None:
    """Stringify an optional snapshot path for log entries."""
    return str(path) if path is not None else None


# ---------------------------------------------------------------------------
# Metadata + log models
# ---------------------------------------------------------------------------


class SkillMeta(BaseModel):
    """Per-skill metadata persisted at ``<skill_dir>/skill.meta.json``.

    Carries the :class:`SkillSpec` and ``collection_id`` so the Update use case
    can reload the authored shape and locate the old Schema collection without
    asking the user again (FR-10 idempotent re-runs).
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    skill_name: str
    collection_id: str
    spec: SkillSpec
    publish_status: PublishStatus = PublishStatus.PUBLISHED
    built_at: str
    last_published_at: str | None = None
    transaction_id: str | None = None


class PublishLogEntry(BaseModel):
    """One record appended to ``wiki/publish-log.jsonl``."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    timestamp: str
    action: Literal["publish", "rollback"]
    skill_name: str
    skill_dir: str
    snapshot_path: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    success: bool = True
    transaction_id: str | None = None
    publish_id: str | None = None
    artifact_id: str | None = None


@dataclass
class PublishRecord:
    """Outcome of a publish or rollback, returned to the use case / CLI."""

    skill_name: str
    skill_dir: Path
    action: str  # "publish" | "rollback"
    published_at: str
    snapshot_path: Path | None = None
    counts: dict[str, int] = field(default_factory=dict)
    transaction_id: str | None = None
    artifact_id: str | None = None
    publish_id: str | None = None


class PublishTransaction(BaseModel):
    """Durable directory-swap journal used for crash recovery."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    publish_id: str
    state: Literal[
        "prepared", "snapshotted", "swapped", "committed", "rollback_required"
    ]
    skill_name: str
    skill_dir: str
    staging_dir: str
    snapshot_dir: str | None = None
    version_id: str
    published_at: str
    counts: dict[str, int] = Field(default_factory=dict)
    transaction_id: str | None = None
    artifact_id: str | None = None


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------


class Publisher:
    """Atomically publish a compiled Skill tree with snapshot + rollback.

    Parameters:
        data_home: Root under which ``wiki/``, ``snapshots/``, ``.staging/``
            and (typically) ``skills/`` live. All renames stay on the same
            volume to avoid cross-device rename failures.
        budget: Optional token budget override for the compiled SKILL.md
            (tests use a tight ceiling to exercise the budget failure path).
        now: Optional clock for deterministic timestamps in tests.
    """

    def __init__(
        self,
        data_home: Path,
        *,
        budget: TokenBudget | None = None,
        now: Callable[[], _dt.datetime] | None = None,
    ) -> None:
        self._data_home = data_home.resolve()
        self._wiki = self._data_home / "wiki"
        self._snapshots = self._data_home / "snapshots"
        self._staging = self._data_home / ".staging"
        self._transactions = self._data_home / ".transactions" / "publish"
        self._active = self._data_home / ".active"
        self._budget = budget
        self._now = now or _now
        self._history_by_skill = self._load_index_history()
        self._recover_publish_transactions()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(
        self,
        skill_dir: Path,
        units: list[KnowledgeUnit],
        spec: SkillSpec,
        *,
        collection_id: str,
        source_manifests: list[SourceManifest],
        unresolved_conflicts: list[str],
        counts: dict[str, int] | None = None,
        transaction_id: str | None = None,
        analysis_run: AnalysisRunManifest | None = None,
    ) -> PublishRecord:
        """Compile *units* into a new Skill tree and atomically swap it in.

        Flow: preflight → stage → compile → validate → write real quality
        report + meta → snapshot old → swap → update index + log. Any failure
        after the snapshot triggers automatic rollback to the snapshot.
        Failures before the snapshot leave the published tree untouched; the
        staging dir is cleaned up.
        """
        counts = counts or {}
        now = self._now()
        skill_dir = Path(skill_dir)
        staging = self._staging / f"{spec.name}-{_ts(now)}"
        snapshot_dir: Path | None = None
        swapped = False
        publish_tx: PublishTransaction | None = None

        self._ensure_publishable(skill_dir, spec, units, unresolved_conflicts)

        try:
            self._compile(staging, units, spec, source_manifests)
            self._validate_staging(staging, now)
            self._write_meta(
                staging,
                spec,
                collection_id,
                now,
                existing_dir=skill_dir,
                transaction_id=transaction_id,
            )
            if analysis_run is not None:
                atomic_write(
                    staging / "analysis-run.json",
                    analysis_run.model_dump_json(indent=2),
                )
            artifact = write_compilation_artifact(
                staging,
                collection_id=collection_id,
                spec=spec,
                units=units,
                source_manifests=source_manifests,
            )
            if not verify_compilation_artifact(staging, artifact, units=units):
                raise DomainError(
                    code=ErrorCode.PUBLISH_FAILED,
                    input_id=str(staging),
                    message="The staged compilation artifact is inconsistent.",
                    recovery="Rebuild the Skill and retry publication.",
                )

            publish_tx = self._begin_publish_transaction(
                skill_name=spec.name,
                skill_dir=skill_dir,
                staging_dir=staging,
                version_id=artifact.artifact_id,
                published_at=_iso(now),
                counts=counts,
                transaction_id=transaction_id,
                artifact_id=artifact.artifact_id,
            )

            if skill_dir.exists():
                snapshot_dir = self._snapshot_dir(spec.name, now)
                publish_tx = self._update_publish_transaction(
                    publish_tx, state="prepared", snapshot_dir=snapshot_dir
                )
                self._rename(skill_dir, snapshot_dir)
                publish_tx = self._update_publish_transaction(
                    publish_tx, state="snapshotted", snapshot_dir=snapshot_dir
                )

            self._swap(staging, skill_dir)
            swapped = True
            publish_tx = self._update_publish_transaction(
                publish_tx, state="swapped", snapshot_dir=snapshot_dir
            )
            self._write_active_pointer(
                spec.name,
                version_id=publish_tx.version_id,
                skill_dir=skill_dir,
                transaction_id=transaction_id,
            )

            record = PublishRecord(
                skill_name=spec.name,
                skill_dir=skill_dir,
                action="publish",
                published_at=_iso(now),
                snapshot_path=snapshot_dir,
                counts=counts,
                transaction_id=transaction_id,
                artifact_id=artifact.artifact_id,
                publish_id=publish_tx.publish_id,
            )
            self._append_log(
                PublishLogEntry(
                    timestamp=_iso(now),
                    action="publish",
                    skill_name=spec.name,
                    skill_dir=str(skill_dir),
                    snapshot_path=_snap_str(record.snapshot_path),
                    counts=counts,
                    success=True,
                    transaction_id=transaction_id,
                    publish_id=publish_tx.publish_id,
                    artifact_id=artifact.artifact_id,
                )
            )
            self._update_index()
            publish_tx = self._update_publish_transaction(
                publish_tx, state="committed", snapshot_dir=snapshot_dir
            )
            self._remove_publish_transaction(publish_tx)
            return record
        except Exception as exc:
            # Clean up the staging tree whether or not we got past the swap.
            self._cleanup(staging)
            snapshot_available = snapshot_dir is not None and snapshot_dir.exists()
            if not snapshot_available and not swapped:
                # Compile/preflight/validation failed before the commit point;
                # the published slot was never touched.
                raise

            restored = (
                self._restore_on_failure(skill_dir, snapshot_dir)
                if snapshot_available and snapshot_dir is not None
                else self._remove_first_publish(skill_dir)
            )
            if not restored:
                raise DomainError(
                    code=ErrorCode.PUBLISH_ROLLBACK_FAILED,
                    input_id=str(skill_dir),
                    message=(
                        "Publish failed after the directory swap and automatic "
                        "recovery also failed."
                    ),
                    recovery=(
                        "Inspect the published slot and snapshots before "
                        "retrying; manual restoration may be required."
                    ),
                    details={"publish_error": type(exc).__name__},
                ) from exc

            if publish_tx is not None:
                self._remove_publish_transaction(publish_tx)
            if snapshot_available:
                restored_meta = load_skill_meta(skill_dir)
                self._write_active_pointer(
                    spec.name,
                    version_id=_ts(now),
                    skill_dir=skill_dir,
                    transaction_id=(
                        restored_meta.transaction_id if restored_meta else None
                    ),
                )
            else:
                self._remove_active_pointer(spec.name)

            self._record_failed_publish(
                skill_dir=skill_dir,
                spec=spec,
                now=now,
                snapshot_dir=snapshot_dir,
                counts=counts,
                transaction_id=transaction_id,
                publish_id=publish_tx.publish_id if publish_tx else None,
            )
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id=str(skill_dir),
                message=f"Publish failed and the prior state was restored: {exc}",
                recovery="Resolve the underlying error and retry publication.",
                details={"publish_error": type(exc).__name__},
            ) from exc

    def rollback_latest(self, skill_dir: Path) -> PublishRecord:
        """Restore the latest snapshot of *skill_dir*.

        The current tree is itself moved to a new snapshot so the rollback is
        reversible by another rollback call. Raises
        :class:`StorageNotFoundError` when no snapshot exists.
        """
        now = self._now()
        skill_dir = Path(skill_dir)
        name = skill_dir.name
        latest = self._latest_snapshot(name)
        if latest is None:
            raise StorageNotFoundError(name, str(self._snapshots / name))

        new_snapshot: Path | None = None
        try:
            if skill_dir.exists():
                new_snapshot = self._snapshot_dir(name, now)
                self._rename(skill_dir, new_snapshot)
            self._rename(latest, skill_dir)
            restored_meta = load_skill_meta(skill_dir)
            restored_artifact = load_compilation_artifact(skill_dir)
            self._write_active_pointer(
                name,
                version_id=(
                    restored_artifact.artifact_id
                    if restored_artifact is not None
                    else _ts(now)
                ),
                skill_dir=skill_dir,
                transaction_id=(
                    restored_meta.transaction_id if restored_meta else None
                ),
            )
        except Exception as exc:
            if new_snapshot is not None and new_snapshot.exists():
                self._restore_on_failure(skill_dir, new_snapshot)
            raise DomainError(
                code=ErrorCode.PUBLISH_ROLLBACK_FAILED,
                input_id=str(skill_dir),
                message=f"Rollback failed: {exc}",
                recovery="Resolve the underlying error and re-run rollback.",
            ) from exc

        record = PublishRecord(
            skill_name=name,
            skill_dir=skill_dir,
            action="rollback",
            published_at=_iso(now),
            snapshot_path=new_snapshot,
            counts={},
            artifact_id=(
                restored_artifact.artifact_id
                if restored_artifact is not None
                else None
            ),
        )
        self._append_log(
            PublishLogEntry(
                timestamp=_iso(now),
                action="rollback",
                skill_name=name,
                skill_dir=str(skill_dir),
                snapshot_path=_snap_str(new_snapshot),
                counts={},
                success=True,
                artifact_id=record.artifact_id,
            )
        )
        self._update_index()
        return record

    # ------------------------------------------------------------------
    # Compile + meta
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_publishable(
        skill_dir: Path,
        spec: SkillSpec,
        units: list[KnowledgeUnit],
        unresolved_conflicts: list[str],
    ) -> None:
        """Reject incomplete review state before compiling any files."""
        if skill_dir.name != spec.name:
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id=str(skill_dir),
                message="Skill directory name must match SkillSpec.name.",
                recovery=(
                    "Publish to a directory whose final component is the "
                    "Skill slug."
                ),
                details={
                    "directory_name": skill_dir.name,
                    "skill_name": spec.name,
                },
            )
        if not units:
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id="publish",
                message="No approved knowledge units were provided for publication.",
                recovery="Approve at least one reviewed knowledge unit and retry.",
            )

        seen: set[str] = set()
        duplicate_ids: set[str] = set()
        for unit in units:
            if unit.unit_id in seen:
                duplicate_ids.add(unit.unit_id)
            seen.add(unit.unit_id)
        if duplicate_ids:
            ids = sorted(duplicate_ids)
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id="publish",
                message="Duplicate knowledge unit IDs cannot be published.",
                recovery="Resolve duplicate unit records before publishing.",
                details={"unit_ids": ids},
            )

        unapproved = sorted(
            unit.unit_id
            for unit in units
            if str(unit.review_status) != KnowledgeStatus.APPROVED.value
        )
        if unapproved:
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id="publish",
                message="Only approved knowledge units may be published.",
                recovery=(
                    "Review the listed units and explicitly mark accepted "
                    "records as approved before retrying."
                ),
                details={"unit_ids": unapproved},
            )

        conflict_ids = sorted(set(unresolved_conflicts))
        if conflict_ids:
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id="publish",
                message="Unresolved conflicts block publication.",
                recovery="Resolve or explicitly preserve each conflict and retry.",
                details={"conflict_ids": conflict_ids},
            )

    def _validate_staging(self, staging: Path, now: _dt.datetime) -> None:
        """Run the complete quality gate and replace the compiler's stub report."""
        try:
            report = Validator(staging, now=now).validate()
        except Exception as exc:  # noqa: BLE001 - normalize validation boundary
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id=str(staging),
                message="The staged Skill could not be validated.",
                recovery="Resolve the validation error and retry publication.",
                details={"validation_error": type(exc).__name__},
            ) from exc

        gate = evaluate_publication_quality(report)
        missing_checks = list(gate.missing_check_ids)
        not_run_checks = list(gate.not_run_check_ids)
        gate_failed = not gate.publishable
        if gate_failed:
            if report.status != ReportStatus.FAIL:
                report = report.model_copy(update={"status": ReportStatus.FAIL})
            writer = QualityReportWriter(staging)
            diagnostic_errors: list[str] = []
            try:
                writer.write(report)
            except Exception as exc:  # noqa: BLE001 - diagnostic is best effort
                diagnostic_errors.append(f"staging:{type(exc).__name__}")
            report_dir: Path | None = None
            try:
                report_dir = self._archive_quality_report(
                    writer, report, staging.name
                )
            except Exception as exc:  # noqa: BLE001 - preserve gate result
                diagnostic_errors.append(f"archive:{type(exc).__name__}")
            failed_checks = [
                str(check.get("check_id", ""))
                for check in report.checks
                if check.get("status") == "fail"
            ]
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id=str(staging),
                message="The staged Skill failed the publication quality gate.",
                recovery=(
                    "Resolve the failed validation checks and retry publication."
                ),
                details={
                    "quality_status": report.status.value,
                    "failed_checks": failed_checks,
                    "missing_checks": missing_checks,
                    "not_run_checks": not_run_checks,
                    "quality_report": report.model_dump(mode="json"),
                    "quality_report_dir": (
                        str(report_dir) if report_dir is not None else None
                    ),
                    "diagnostic_errors": diagnostic_errors,
                },
            )

        published_report = report.model_copy(update={"published": True})
        try:
            QualityReportWriter(staging).write(published_report)
        except Exception as exc:  # noqa: BLE001 - normalize publish boundary
            raise DomainError(
                code=ErrorCode.PUBLISH_FAILED,
                input_id=str(staging),
                message="The staged quality report could not be written.",
                recovery="Resolve the report storage error and retry publication.",
                details={"report_error": type(exc).__name__},
            ) from exc

    def _archive_quality_report(
        self,
        writer: QualityReportWriter,
        report: QualityReport,
        run_name: str,
    ) -> Path:
        """Persist a failed gate report outside transient staging."""
        base = self._data_home / "quality-reports" / run_name
        report_dir = base
        suffix = 2
        while report_dir.exists():
            report_dir = base.with_name(f"{base.name}-{suffix}")
            suffix += 1
        report_dir.mkdir(parents=True, exist_ok=False)
        atomic_write(report_dir / "quality-report.md", writer.to_markdown(report))
        atomic_write(report_dir / "quality-report.json", writer.to_json(report))
        return report_dir

    def _compile(
        self,
        staging: Path,
        units: list[KnowledgeUnit],
        spec: SkillSpec,
        source_manifests: list[SourceManifest],
    ) -> None:
        """Build the IR and write the Skill tree into *staging*.

        Budget or IR failures raise before any snapshot/swap, so the published
        tree is never touched by a failed compile. A stale staging dir from a
        prior crash is removed first.
        """
        self._cleanup(staging)
        builder = IRBuilder(units, spec)
        ir = builder.build()
        references = builder.build_references()
        wiki_files = WikiGenerator(units, skill_name=spec.name).build()
        writer = SkillWriter(output_dir=staging, budget=self._budget)
        writer.write(
            ir,
            references=references,
            source_manifests=source_manifests,
            wiki_files=wiki_files,
        )

    def _write_meta(
        self,
        target_dir: Path,
        spec: SkillSpec,
        collection_id: str,
        now: _dt.datetime,
        *,
        existing_dir: Path,
        transaction_id: str | None = None,
    ) -> None:
        """Write ``skill.meta.json``, preserving ``built_at`` across updates."""
        built_at = _iso(now)
        existing = load_skill_meta(existing_dir) if existing_dir.exists() else None
        if existing is not None:
            built_at = existing.built_at
        meta = SkillMeta(
            skill_name=spec.name,
            collection_id=collection_id,
            spec=spec,
            publish_status=PublishStatus.PUBLISHED,
            built_at=built_at,
            last_published_at=_iso(now),
            transaction_id=transaction_id,
        )
        atomic_write(target_dir / "skill.meta.json", meta.model_dump_json(indent=2))

    # ------------------------------------------------------------------
    # Crash journal + active pointer
    # ------------------------------------------------------------------

    def _begin_publish_transaction(
        self,
        *,
        skill_name: str,
        skill_dir: Path,
        staging_dir: Path,
        version_id: str,
        published_at: str,
        counts: dict[str, int],
        transaction_id: str | None,
        artifact_id: str,
    ) -> PublishTransaction:
        transaction = PublishTransaction(
            publish_id=uuid.uuid4().hex,
            state="prepared",
            skill_name=skill_name,
            skill_dir=str(skill_dir),
            staging_dir=str(staging_dir),
            version_id=version_id,
            published_at=published_at,
            counts=counts,
            transaction_id=transaction_id,
            artifact_id=artifact_id,
        )
        self._save_publish_transaction(transaction)
        return transaction

    def _update_publish_transaction(
        self,
        transaction: PublishTransaction,
        *,
        state: Literal[
            "prepared", "snapshotted", "swapped", "committed", "rollback_required"
        ],
        snapshot_dir: Path | None = None,
    ) -> PublishTransaction:
        updated = transaction.model_copy(
            update={
                "state": state,
                "snapshot_dir": str(snapshot_dir)
                if snapshot_dir is not None
                else transaction.snapshot_dir,
            }
        )
        self._save_publish_transaction(updated)
        return updated

    def _save_publish_transaction(self, transaction: PublishTransaction) -> None:
        self._transactions.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self._transactions / f"{transaction.publish_id}.json",
            transaction.model_dump_json(indent=2),
        )

    def _remove_publish_transaction(self, transaction: PublishTransaction) -> None:
        try:
            (self._transactions / f"{transaction.publish_id}.json").unlink()
        except FileNotFoundError:
            return

    def _pending_publish_transactions(self) -> list[PublishTransaction]:
        if not self._transactions.exists():
            return []
        pending: list[PublishTransaction] = []
        for path in sorted(self._transactions.glob("*.json")):
            try:
                pending.append(
                    PublishTransaction.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, ValidationError, ValueError):
                # Leave malformed journals in place for manual diagnosis.
                continue
        return pending

    def _recover_publish_transactions(self) -> None:
        """Finish or safely undo directory swaps left by a hard crash."""
        for transaction in self._pending_publish_transactions():
            if transaction.state == "rollback_required":
                raise DomainError(
                    code=ErrorCode.PUBLISH_ROLLBACK_FAILED,
                    input_id=transaction.skill_dir,
                    message="A previous publication requires manual recovery.",
                    recovery="Inspect the active tree and snapshot before retrying.",
                    details={"publish_id": transaction.publish_id},
                )
            target = Path(transaction.skill_dir)
            staging = Path(transaction.staging_dir)
            snapshot = (
                Path(transaction.snapshot_dir)
                if transaction.snapshot_dir
                else None
            )
            try:
                if transaction.state == "committed":
                    self._remove_publish_transaction(transaction)
                    continue
                if transaction.state in {"prepared", "snapshotted"}:
                    if (
                        snapshot is not None
                        and snapshot.exists()
                        and not target.exists()
                    ):
                        if staging.exists():
                            self._rename(staging, target)
                            transaction = self._update_publish_transaction(
                                transaction, state="swapped", snapshot_dir=snapshot
                            )
                        else:
                            self._rename(snapshot, target)
                            self._remove_active_pointer(transaction.skill_name)
                            self._remove_publish_transaction(transaction)
                            continue
                    else:
                        self._cleanup(staging)
                        self._remove_publish_transaction(transaction)
                        continue
                if transaction.state == "swapped":
                    if target.exists():
                        self._write_active_pointer(
                            transaction.skill_name,
                            version_id=transaction.version_id,
                            skill_dir=target,
                            transaction_id=transaction.transaction_id,
                        )
                        if transaction.artifact_id is not None:
                            artifact = load_compilation_artifact(target)
                            if (
                                artifact is None
                                or artifact.artifact_id != transaction.artifact_id
                                or not verify_compilation_artifact(target, artifact)
                            ):
                                raise ValueError(
                                    "active Skill compilation artifact failed "
                                    "verification"
                                )
                        self._ensure_success_log(transaction)
                        self._update_index()
                        self._remove_publish_transaction(transaction)
                    elif snapshot is not None and snapshot.exists():
                        self._rename(snapshot, target)
                        self._remove_active_pointer(transaction.skill_name)
                        self._remove_publish_transaction(transaction)
                    else:
                        raise FileNotFoundError(
                            "neither active nor snapshot tree exists"
                        )
            except Exception as exc:  # noqa: BLE001 - recovery boundary
                with suppress(OSError):
                    self._update_publish_transaction(
                        transaction,
                        state="rollback_required",
                        snapshot_dir=snapshot,
                    )
                raise DomainError(
                    code=ErrorCode.PUBLISH_ROLLBACK_FAILED,
                    input_id=transaction.skill_dir,
                    message="An incomplete publication could not be recovered.",
                    recovery=(
                        "Inspect the staging, active and snapshot directories "
                        "manually."
                    ),
                    details={
                        "publish_id": transaction.publish_id,
                        "error": type(exc).__name__,
                    },
                ) from exc

    def _ensure_success_log(self, transaction: PublishTransaction) -> None:
        entries = self._read_log()
        if any(
            entry.publish_id == transaction.publish_id and entry.success
            for entry in entries
        ):
            return
        self._append_log(
            PublishLogEntry(
                timestamp=transaction.published_at,
                action="publish",
                skill_name=transaction.skill_name,
                skill_dir=transaction.skill_dir,
                snapshot_path=transaction.snapshot_dir,
                counts=transaction.counts,
                success=True,
                transaction_id=transaction.transaction_id,
                publish_id=transaction.publish_id,
                artifact_id=transaction.artifact_id,
            )
        )

    def _write_active_pointer(
        self,
        skill_name: str,
        *,
        version_id: str,
        skill_dir: Path,
        transaction_id: str | None,
    ) -> None:
        self._active.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self._active / f"{skill_name}.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "skill_name": skill_name,
                    "version_id": version_id,
                    "skill_dir": str(skill_dir),
                    "transaction_id": transaction_id,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    def _remove_active_pointer(self, skill_name: str) -> None:
        try:
            (self._active / f"{skill_name}.json").unlink()
        except FileNotFoundError:
            return

    # ------------------------------------------------------------------
    # Directory rename / swap / snapshot
    # ------------------------------------------------------------------

    def _snapshot_dir(self, name: str, now: _dt.datetime) -> Path:
        """Compute a non-existing snapshot path for *name* at *now*.

        A uniqueness suffix is appended when the base path already exists so
        that repeated publishes within the same clock tick (common in tests
        with a fixed clock) never collide. Suffixes sort after the base, so
        ``max``-by-name in :meth:`_latest_snapshot` still picks the newest.
        """
        base = self._snapshots / name / _ts(now)
        if not base.exists():
            return base
        i = 2
        while True:
            cand = base.with_name(base.name + f"-{i}")
            if not cand.exists():
                return cand
            i += 1

    def _latest_snapshot(self, name: str) -> Path | None:
        root = self._snapshots / name
        if not root.exists():
            return None
        candidates = [p for p in root.iterdir() if p.is_dir()]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.name)

    @staticmethod
    def _rename(src: Path, dst: Path) -> None:
        """Rename *src* to *dst*, creating the parent and asserting absence.

        ``os.replace`` renames a non-empty directory to a non-existent target
        on both Windows and POSIX; we ensure the target's parent exists and the
        target is absent so the rename is atomic.
        """
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise FileExistsError(
                f"Snapshot/slot target already exists: {dst}"
            )
        os.replace(src, dst)

    def _swap(self, staging: Path, skill_dir: Path) -> None:
        """Move the staged tree into the published slot.

        Precondition: ``skill_dir`` does not exist (the old tree was already
        snapshotted) so the rename target is absent.
        """
        skill_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, skill_dir)

    def _restore_on_failure(
        self, skill_dir: Path, snapshot_dir: Path
    ) -> bool:
        """Restore *snapshot_dir* back into *skill_dir* after a failed swap.

        Returns whether restoration completed. If the slot contains the new
        tree, it is moved to a unique sibling before the snapshot is restored.
        """
        displaced: Path | None = None
        try:
            if skill_dir.exists():
                displaced = self._unique_sibling(skill_dir, ".failed")
                os.replace(skill_dir, displaced)
            os.replace(snapshot_dir, skill_dir)
        except OSError:
            if (
                displaced is not None
                and displaced.exists()
                and not skill_dir.exists()
            ):
                try:
                    os.replace(displaced, skill_dir)
                except OSError:
                    return False
            return False
        if displaced is not None:
            shutil.rmtree(displaced, ignore_errors=True)
        return skill_dir.exists() and not snapshot_dir.exists()

    @staticmethod
    def _unique_sibling(path: Path, suffix: str) -> Path:
        candidate = path.with_name(path.name + suffix)
        index = 2
        while candidate.exists():
            candidate = path.with_name(f"{path.name}{suffix}-{index}")
            index += 1
        return candidate

    def _remove_first_publish(self, skill_dir: Path) -> bool:
        """Undo a failed first publish that had already reached the slot."""
        self._cleanup(skill_dir)
        return not skill_dir.exists()

    @staticmethod
    def _cleanup(staging: Path) -> None:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    def _record_failed_publish(
        self,
        *,
        skill_dir: Path,
        spec: SkillSpec,
        now: _dt.datetime,
        snapshot_dir: Path | None,
        counts: dict[str, int],
        transaction_id: str | None,
        publish_id: str | None,
    ) -> None:
        """Best-effort failure journal; never masks the triggering error."""
        try:
            self._append_log(
                PublishLogEntry(
                    timestamp=_iso(now),
                    action="publish",
                    skill_name=spec.name,
                    skill_dir=str(skill_dir),
                    snapshot_path=_snap_str(snapshot_dir),
                    counts=counts,
                    success=False,
                    transaction_id=transaction_id,
                    publish_id=publish_id,
                )
            )
            self._update_index()
        except OSError:
            return

    # ------------------------------------------------------------------
    # Index + log (derived view + append-only)
    # ------------------------------------------------------------------

    def _log_path(self) -> Path:
        return self._wiki / "publish-log.jsonl"

    def _append_log(self, entry: PublishLogEntry) -> None:
        path = self._log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = entry.model_dump_json()
        if path.exists():
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        else:
            atomic_write(path, line + "\n")
        self._apply_log_entry(entry)

    def _read_log(self) -> list[PublishLogEntry]:
        path = self._log_path()
        if not path.exists():
            return []
        entries: list[PublishLogEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(PublishLogEntry.model_validate_json(line))
            except (ValidationError, ValueError):
                continue
        return entries

    def _update_index(self) -> None:
        """Regenerate ``wiki/index.md`` from the append-only log.

        Keeps only the latest committed entry per skill. A failed publish
        record cancels the immediately preceding success record when both
        describe the same attempt. This matters when the directory swap and
        success log completed but index regeneration failed: recovery restores
        the prior tree, and a later index rebuild must not resurrect the
        compensated publication.
        """
        latest_by_skill = {
            skill_name: history[-1]
            for skill_name, history in self._history_by_skill.items()
            if history
        }
        lines = [
            "# Published Skills",
            "",
            "Derived from `publish-log.jsonl` (latest entry per skill).",
            "",
            "| Skill | Path | Last Action | Timestamp | Snapshot |",
            "|---|---|---|---|---|",
        ]
        for name in sorted(latest_by_skill):
            e = latest_by_skill[name]
            snap = e.snapshot_path or "—"
            lines.append(
                f"| {name} | {e.skill_dir} | {e.action} | {e.timestamp} | {snap} |"
            )
        atomic_write(self._wiki / "index.md", "\n".join(lines) + "\n")

    def _load_index_history(self) -> dict[str, list[PublishLogEntry]]:
        """Load the JSONL log once when a Publisher instance starts."""
        history_by_skill: dict[str, list[PublishLogEntry]] = {}
        for entry in self._read_log():
            self._apply_log_entry(entry, history_by_skill=history_by_skill)
        return history_by_skill

    def _apply_log_entry(
        self,
        entry: PublishLogEntry,
        *,
        history_by_skill: dict[str, list[PublishLogEntry]] | None = None,
    ) -> None:
        """Incrementally update latest-publish state after one durable log write."""
        histories = (
            self._history_by_skill if history_by_skill is None else history_by_skill
        )
        if entry.success:
            histories.setdefault(entry.skill_name, []).append(entry)
            return
        history = histories.get(entry.skill_name, [])
        if history and self._same_publish_attempt(history[-1], entry):
            history.pop()

    @staticmethod
    def _same_publish_attempt(
        successful: PublishLogEntry, failed: PublishLogEntry
    ) -> bool:
        """Return whether *failed* compensates *successful*."""
        return (
            successful.action == failed.action == "publish"
            and successful.timestamp == failed.timestamp
            and successful.skill_dir == failed.skill_dir
            and successful.snapshot_path == failed.snapshot_path
            and successful.counts == failed.counts
            and successful.transaction_id == failed.transaction_id
            and successful.publish_id == failed.publish_id
        )


__all__ = [
    "Publisher",
    "PublishRecord",
    "SkillMeta",
    "PublishLogEntry",
    "PublishTransaction",
    "load_skill_meta",
]


def load_skill_meta(skill_dir: Path) -> SkillMeta | None:
    """Read ``<skill_dir>/skill.meta.json`` if present, else return ``None``.

    Public so :class:`~book2skill.application.update.UpdateUseCase` can locate
    the old collection and reuse the authored spec without re-asking.
    """
    path = skill_dir / "skill.meta.json"
    if not path.exists():
        return None
    try:
        return SkillMeta.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError):
        return None
