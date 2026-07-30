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
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from book2skill.compiler import IRBuilder, SkillSpec, SkillWriter
from book2skill.compiler.token_budget import TokenBudget
from book2skill.domain import (
    DomainError,
    ErrorCode,
    KnowledgeUnit,
    PublishStatus,
    SourceManifest,
)
from book2skill.storage import atomic_write
from book2skill.storage.errors import StorageNotFoundError


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


@dataclass
class PublishRecord:
    """Outcome of a publish or rollback, returned to the use case / CLI."""

    skill_name: str
    skill_dir: Path
    action: str  # "publish" | "rollback"
    published_at: str
    snapshot_path: Path | None = None
    counts: dict[str, int] = field(default_factory=dict)


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
        self._budget = budget
        self._now = now or _now

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
        source_manifests: list[SourceManifest] | None = None,
        counts: dict[str, int] | None = None,
    ) -> PublishRecord:
        """Compile *units* into a new Skill tree and atomically swap it in.

        Flow: stage → compile (budget-gated) → write meta → snapshot old →
        swap → update index + log. Any failure after the snapshot triggers
        automatic rollback to the snapshot. Failures before the snapshot leave
        the published tree untouched; the staging dir is cleaned up.
        """
        counts = counts or {}
        now = self._now()
        skill_dir = Path(skill_dir)
        staging = self._staging / f"{spec.name}-{_ts(now)}"
        snapshot_dir: Path | None = None

        try:
            self._compile(staging, units, spec, source_manifests)
            self._write_meta(
                staging, spec, collection_id, now, existing_dir=skill_dir
            )

            if skill_dir.exists():
                snapshot_dir = self._snapshot_dir(spec.name, now)
                self._rename(skill_dir, snapshot_dir)

            self._swap(staging, skill_dir)
        except Exception as exc:
            # Clean up the staging tree whether or not we got past the swap.
            self._cleanup(staging)
            if snapshot_dir is not None and snapshot_dir.exists():
                # A snapshot exists → the old tree was moved aside. Restore it
                # so the published slot is never left empty or half-swapped.
                self._restore_on_failure(skill_dir, snapshot_dir)
                self._append_log(
                    PublishLogEntry(
                        timestamp=_iso(now),
                        action="publish",
                        skill_name=spec.name,
                        skill_dir=str(skill_dir),
                        snapshot_path=str(snapshot_dir),
                        counts=counts,
                        success=False,
                    )
                )
                raise DomainError(
                    code=ErrorCode.PUBLISH_FAILED,
                    input_id=str(skill_dir),
                    message=f"Publish failed and was rolled back: {exc}",
                    recovery=(
                        "The previous Skill version was restored from the "
                        f"snapshot at {snapshot_dir}. Re-run after resolving "
                        "the underlying error."
                    ),
                ) from exc
            # No snapshot taken yet → published tree untouched. Re-raise.
            raise

        # On success ``snapshot_dir`` is the snapshot we just took (or None
        # for a first publish with no prior tree).
        record = PublishRecord(
            skill_name=spec.name,
            skill_dir=skill_dir,
            action="publish",
            published_at=_iso(now),
            snapshot_path=snapshot_dir,
            counts=counts,
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
            )
        )
        self._update_index()
        return record

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
            )
        )
        self._update_index()
        return record

    # ------------------------------------------------------------------
    # Compile + meta
    # ------------------------------------------------------------------

    def _compile(
        self,
        staging: Path,
        units: list[KnowledgeUnit],
        spec: SkillSpec,
        source_manifests: list[SourceManifest] | None,
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
        writer = SkillWriter(output_dir=staging, budget=self._budget)
        writer.write(ir, references=references, source_manifests=source_manifests)

    def _write_meta(
        self,
        target_dir: Path,
        spec: SkillSpec,
        collection_id: str,
        now: _dt.datetime,
        *,
        existing_dir: Path,
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
        )
        atomic_write(target_dir / "skill.meta.json", meta.model_dump_json(indent=2))

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
    ) -> None:
        """Restore *snapshot_dir* back into *skill_dir* after a failed swap.

        If the slot is occupied by a half-swapped tree, move it aside first;
        this is best-effort and must not raise (caller already failing).
        """
        try:
            if skill_dir.exists():
                # A partial tree sits in the slot; trash it to make room.
                trash = skill_dir.with_name(skill_dir.name + ".broken")
                if trash.exists():
                    shutil.rmtree(trash, ignore_errors=True)
                os.replace(skill_dir, trash)
                shutil.rmtree(trash, ignore_errors=True)
            os.replace(snapshot_dir, skill_dir)
        except OSError:
            # We are already in a failure path; do not mask the original error.
            pass

    @staticmethod
    def _cleanup(staging: Path) -> None:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

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

        Keeps only the latest entry per skill_name so the registry reflects
        the current published state without a second source of truth.
        """
        entries = self._read_log()
        latest_by_skill: dict[str, PublishLogEntry] = {}
        for e in entries:
            latest_by_skill[e.skill_name] = e
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


__all__ = [
    "Publisher",
    "PublishRecord",
    "SkillMeta",
    "PublishLogEntry",
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
