"""Read-only Legacy Skill migration audit for B2S-M13-06.

Legacy v1 Skill trees remain usable as historical artifacts, but they are not
silently upgraded to Standalone products.  This module only classifies a
tree, verifies an explicitly supplied production Runtime Closure, and returns
an auditable plan.  It deliberately has no write or activation operation.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from book2skill.domain import SourceManifest
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.runtime.closure import (
    ClosureLifecycle,
    RuntimeClosureError,
    RuntimeClosureManifest,
    RuntimeClosureSession,
    validate_closure,
)
from book2skill.runtime.defaults import default_runtime_contract
from book2skill.runtime.emission import RuntimeProductEmitter
from book2skill.storage.file_storage import atomic_write


class MigrationDisposition(StrEnum):
    """Classification returned by the read-only migration audit."""

    LEGACY_UNCLOSED = "legacy-unclosed"
    CLOSURE_READY = "closure-ready"
    BLOCKED = "blocked"


class LegacySkillAssessment(BaseModel):
    """Safe, path-free evidence for one existing Skill tree."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    skill_id: str = Field(..., min_length=1)
    disposition: MigrationDisposition
    manifest_present: bool
    closure_hash: str | None = None
    reason_codes: tuple[str, ...] = ()


class LegacyMigrationPlan(BaseModel):
    """A reviewable migration inventory with no implicit write permission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    assessments: tuple[LegacySkillAssessment, ...] = Field(..., min_length=1)
    auto_migrate: Literal[False] = False
    manual_approval_required: Literal[True] = True

    @property
    def legacy_unclosed(self) -> tuple[LegacySkillAssessment, ...]:
        return tuple(
            item
            for item in self.assessments
            if item.disposition == MigrationDisposition.LEGACY_UNCLOSED
        )

    @property
    def closure_ready(self) -> tuple[LegacySkillAssessment, ...]:
        return tuple(
            item
            for item in self.assessments
            if item.disposition == MigrationDisposition.CLOSURE_READY
        )

    @property
    def blocked(self) -> tuple[LegacySkillAssessment, ...]:
        return tuple(
            item
            for item in self.assessments
            if item.disposition == MigrationDisposition.BLOCKED
        )


class MigrationError(DomainError):
    """Stable error for invalid audit inputs or forbidden auto-migration."""

    def __init__(
        self,
        code: ErrorCode,
        skill_id: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code,
            skill_id,
            message,
            recovery=(
                "Review the inventory and explicitly approve a separate "
                "migration change."
            ),
            details=details,
        )


@dataclass(frozen=True)
class LegacyMigrationResult:
    """Safe operational result for one migrated Skill directory."""

    skill_id: str
    migrated: bool
    closure_hash: str | None = None
    backup_path: Path | None = None
    reason: str | None = None


_SOURCE_REF_RE = re.compile(r"^\s*-\s*([^\s/]+)\s*/\s*([^\s]+)\s*$")
_TS = "%Y%m%dT%H%M%S%fZ"


def _safe_skill_id(skill_dir: Path) -> str:
    """Use only a bounded directory name in reports and error input IDs."""

    name = skill_dir.name.strip()
    return name or "unknown-skill"


class LegacyMigrationAuditor:
    """Inspect existing Skill trees without modifying their contents."""

    def inspect(self, skill_dir: Path) -> LegacySkillAssessment:
        root = Path(skill_dir)
        skill_id = _safe_skill_id(root)
        manifest_path = root / "runtime-closure.json"
        if not root.is_dir() or not (root / "SKILL.md").is_file():
            return self._blocked(
                skill_id,
                "MIGRATION_INVALID",
                None,
                manifest_present=manifest_path.is_file(),
            )
        if not manifest_path.is_file():
            return LegacySkillAssessment(
                skill_id=skill_id,
                disposition=MigrationDisposition.LEGACY_UNCLOSED,
                manifest_present=False,
                reason_codes=("MIGRATION_LEGACY_UNCLOSED",),
            )

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = RuntimeClosureManifest.model_validate(payload)
            report = validate_closure(root, manifest)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            return self._blocked(skill_id, "MIGRATION_CLOSURE_INVALID", exc)
        except RuntimeClosureError as exc:
            return self._blocked(skill_id, str(exc.code), exc)
        except ValueError as exc:
            return self._blocked(skill_id, "MIGRATION_CLOSURE_INVALID", exc)

        return LegacySkillAssessment(
            skill_id=skill_id,
            disposition=MigrationDisposition.CLOSURE_READY,
            manifest_present=True,
            closure_hash=report.closure_hash,
        )

    def plan(self, skill_dirs: Iterable[Path]) -> LegacyMigrationPlan:
        """Build a path-free inventory; never writes or activates a Skill."""

        roots = list(skill_dirs)
        if not roots:
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                "migration",
                "Migration audit requires at least one Skill directory",
            )
        assessments = tuple(self.inspect(root) for root in roots)
        skill_ids = [item.skill_id for item in assessments]
        if len(skill_ids) != len(set(skill_ids)):
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                "migration",
                "Migration audit received duplicate Skill identities",
            )
        return LegacyMigrationPlan(assessments=assessments)

    def require_manual_approval(self, plan: LegacyMigrationPlan) -> None:
        """Make the no-auto-migration boundary explicit to callers."""

        raise MigrationError(
            ErrorCode.MIGRATION_APPROVAL_REQUIRED,
            "migration",
            "Legacy migration requires an explicit approved change; audit is read-only",
            details={"skill_ids": tuple(item.skill_id for item in plan.assessments)},
        )

    @staticmethod
    def _blocked(
        skill_id: str,
        reason: str,
        cause: BaseException | None,
        *,
        manifest_present: bool = True,
    ) -> LegacySkillAssessment:
        del cause  # Never persist exception text, paths or source content.
        return LegacySkillAssessment(
            skill_id=skill_id,
            disposition=MigrationDisposition.BLOCKED,
            manifest_present=manifest_present,
            reason_codes=(reason,),
        )


class LegacyMigrator:
    """Convert reviewed legacy trees into production Runtime Products.

    The migrator has an explicit approval boundary and a two-phase filesystem
    transaction: all trees are staged and validated first, then originals are
    moved to a caller-owned backup root before the staged trees are activated.
    Any activation failure restores every tree committed in this batch.
    """

    def __init__(self, auditor: LegacyMigrationAuditor | None = None) -> None:
        self._auditor = auditor or LegacyMigrationAuditor()

    def migrate(
        self,
        skill_dirs: Iterable[Path],
        *,
        approved_skill_ids: Iterable[str] = (),
        backup_root: Path,
        confirm: bool = False,
    ) -> tuple[LegacyMigrationResult, ...]:
        roots = tuple(Path(item).resolve() for item in skill_dirs)
        plan = self._auditor.plan(roots)
        legacy = plan.legacy_unclosed
        approved = {item.strip() for item in approved_skill_ids if item.strip()}
        required = {item.skill_id for item in legacy}
        if not confirm or not required.issubset(approved):
            raise MigrationError(
                ErrorCode.MIGRATION_APPROVAL_REQUIRED,
                "migration",
                "Batch migration requires explicit confirmation for every legacy Skill",
                details={"skill_ids": tuple(sorted(required))},
            )
        if plan.blocked:
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                "migration",
                "Migration is blocked by an invalid Skill tree",
                details={"skill_ids": tuple(item.skill_id for item in plan.blocked)},
            )

        backup = Path(backup_root).resolve()
        prepared: list[_PreparedMigration] = []
        try:
            for root, assessment in zip(roots, plan.assessments, strict=True):
                if assessment.disposition == MigrationDisposition.CLOSURE_READY:
                    prepared.append(
                        _PreparedMigration(
                            root=root,
                            skill_id=assessment.skill_id,
                            staging=None,
                            closure_hash=assessment.closure_hash,
                        )
                    )
                    continue
                prepared.append(self._prepare(root, assessment.skill_id))
        except MigrationError:
            self._cleanup_prepared(prepared)
            raise

        committed: list[_CommittedMigration] = []
        try:
            for item in prepared:
                if item.staging is None:
                    continue
                self._ensure_backup_root_safe(item.root, backup)
                backup_path = self._backup_path(backup, item.skill_id)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(item.root, backup_path)
                try:
                    os.replace(item.staging, item.root)
                except Exception:
                    os.replace(backup_path, item.root)
                    raise
                committed.append(
                    _CommittedMigration(
                        root=item.root,
                        backup_path=backup_path,
                        skill_id=item.skill_id,
                        closure_hash=item.closure_hash,
                    )
                )
        except Exception as exc:
            for rollback_item in reversed(committed):
                self._rollback_one(rollback_item)
            self._cleanup_prepared(prepared)
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                "migration",
                "Batch migration activation failed and committed trees were restored",
            ) from exc
        finally:
            self._cleanup_prepared(prepared)

        committed_by_id = {item.skill_id: item for item in committed}
        results: list[LegacyMigrationResult] = []
        for item in prepared:
            committed_item = committed_by_id.get(item.skill_id)
            results.append(
                LegacyMigrationResult(
                    skill_id=item.skill_id,
                    migrated=item.staging is not None,
                    closure_hash=item.closure_hash,
                    backup_path=(
                        committed_item.backup_path if committed_item else None
                    ),
                    reason=(
                        None
                        if item.staging is not None
                        else "already-closure-ready"
                    ),
                )
            )
        return tuple(results)

    def _prepare(self, root: Path, skill_id: str) -> _PreparedMigration:
        if not root.is_dir() or not (root / "SKILL.md").is_file():
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                skill_id,
                "Legacy Skill directory is missing required files",
            )
        manifests = self._load_source_manifests(root, skill_id)
        refs = self._load_source_refs(root, skill_id, manifests)
        product, closure_spec = default_runtime_contract(skill_id)
        staging = root.parent / f".{skill_id}.migration-{uuid.uuid4().hex}"
        try:
            shutil.copytree(root, staging)
            emission = RuntimeProductEmitter.emit(
                staging,
                product,
                closure_spec,
                lifecycle=ClosureLifecycle.PRODUCTION,
                source_manifests=manifests,
                source_refs=refs,
            )
            RuntimeClosureSession.open(staging)
            self._mark_published(staging, skill_id)
            return _PreparedMigration(
                root=root,
                skill_id=skill_id,
                staging=staging,
                closure_hash=emission.closure_manifest.closure_hash,
            )
        except MigrationError:
            self._remove_tree(staging)
            raise
        except Exception as exc:
            self._remove_tree(staging)
            raise MigrationError(
                ErrorCode.MIGRATION_CLOSURE_INVALID,
                skill_id,
                "Legacy Skill could not be converted to a production Closure",
            ) from exc

    @staticmethod
    def _load_source_manifests(
        root: Path, skill_id: str
    ) -> tuple[SourceManifest, ...]:
        path = root / "provenance.yml"
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            rows = payload.get("sources") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                raise ValueError("sources missing")
            manifests = tuple(
                SourceManifest.model_validate(
                    {
                        "schema_version": row.get("schema_version", 1),
                        "source_id": row["source_id"],
                        "version": row.get("version", 1),
                        "original_name": row.get(
                            "original_name", row.get("title")
                        ),
                        "content_sha256": row["content_sha256"],
                        "format": str(row["format"]).lower(),
                        "rights_confirmed": row.get("rights_confirmed", True),
                        "rights_note": row.get("rights_note"),
                        "ingested_at": row["ingested_at"],
                    }
                )
                for row in rows
                if isinstance(row, dict)
            )
            if len(manifests) != len(rows):
                raise ValueError("source row is not an object")
        except Exception as exc:
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                skill_id,
                "Legacy provenance.yml has no valid source manifest ledger",
            ) from exc
        ids = [item.source_id for item in manifests]
        if len(ids) != len(set(ids)):
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                skill_id,
                "Legacy provenance.yml contains duplicate source identities",
            )
        return manifests

    @staticmethod
    def _load_source_refs(
        root: Path,
        skill_id: str,
        manifests: tuple[SourceManifest, ...],
    ) -> tuple[tuple[str, str], ...]:
        path = root / "references" / "provenance.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                skill_id,
                "Legacy Skill has no readable provenance reference ledger",
            ) from exc
        known = {item.source_id for item in manifests}
        refs: set[tuple[str, str]] = set()
        for line in text.splitlines():
            match = _SOURCE_REF_RE.match(line)
            if match is None:
                continue
            source_id, block_id = match.groups()
            if source_id not in known:
                raise MigrationError(
                    ErrorCode.MIGRATION_INVALID,
                    skill_id,
                    "Legacy provenance references an unknown source identity",
                )
            refs.add((source_id, block_id))
        if not refs:
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                skill_id,
                "Legacy Skill has no source block provenance to pin",
            )
        return tuple(sorted(refs))

    @staticmethod
    def _mark_published(root: Path, skill_id: str) -> None:
        path = root / "skill.meta.json"
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("metadata must be an object")
            payload["publish_status"] = "published"
            atomic_write(
                path,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
        except Exception as exc:
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                skill_id,
                "Legacy Skill metadata could not be updated atomically",
            ) from exc

    @staticmethod
    def _backup_path(backup_root: Path, skill_id: str) -> Path:
        stamp = _dt.datetime.now(_dt.UTC).strftime(_TS)
        return backup_root / f"{stamp}-{skill_id}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _ensure_backup_root_safe(root: Path, backup_root: Path) -> None:
        if root == backup_root or backup_root.is_relative_to(root):
            raise MigrationError(
                ErrorCode.MIGRATION_INVALID,
                root.name,
                "Migration backup root must not be inside a Skill tree",
            )

    @staticmethod
    def _rollback_one(item: _CommittedMigration) -> None:
        if item.root.exists():
            LegacyMigrator._remove_tree(item.root)
        if item.backup_path.exists():
            os.replace(item.backup_path, item.root)

    @staticmethod
    def _cleanup_prepared(items: Iterable[_PreparedMigration]) -> None:
        for item in items:
            if item.staging is not None and item.staging.exists():
                LegacyMigrator._remove_tree(item.staging)

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)


@dataclass(frozen=True)
class _PreparedMigration:
    root: Path
    skill_id: str
    staging: Path | None
    closure_hash: str | None


@dataclass(frozen=True)
class _CommittedMigration:
    root: Path
    backup_path: Path
    skill_id: str
    closure_hash: str | None

__all__ = [
    "LegacyMigrationAuditor",
    "LegacyMigrationPlan",
    "LegacySkillAssessment",
    "MigrationDisposition",
    "MigrationError",
    "LegacyMigrationResult",
    "LegacyMigrator",
]
