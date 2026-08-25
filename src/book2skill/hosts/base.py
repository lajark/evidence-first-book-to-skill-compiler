"""Multi-host installer base class (TASK-017, PRD FR-04 / FR-09).

A :class:`HostInstaller` deploys a compiled Skill directory to a host-specific
location (Claude Code / TRAE / Codex / generic project). The workflow is:

1. Resolve the Skill name from ``SKILL.md`` frontmatter (``name`` field).
2. Compute the host-specific install path via :meth:`_target_path`.
3. If a previous installation exists, move it aside to a timestamped backup
   directory (unless ``--no-backup``).
4. Copy the new Skill tree into the install slot.
5. Apply host-specific overlays (e.g., Codex ``agents.md``) via
   :meth:`_apply_overlay`.

Uninstallation reverses step 4 only: it removes the Skill install directory
and leaves the workspace, raw, schema and backup trees untouched.

All operations honour ``dry_run``: the proposed actions are reported without
touching the filesystem. Backups live under ``<backup_dir>/<host>/<skill>/<ts>``;
the default backup root is ``~/.book2skill/backups/`` so backups survive
workspace cleanup.

Design notes
------------

- The install slot is never left half-populated: the new tree is staged in a
  temporary sibling directory then renamed into place via :func:`os.replace`
  (mirrors :class:`~book2skill.application.publisher.Publisher`). On failure
  after the backup was taken, the previous version is restored from the backup.
- The Skill name comes from frontmatter so the on-disk directory always matches
  the published ``name`` slug, not the source directory's name.
- Path traversal on the install slot is rejected via
  :func:`~book2skill.storage.resolve_within` for the project-level hosts.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.runtime.closure import RuntimeClosureSession
from book2skill.runtime.product_manifest import (
    PRODUCT_MANIFEST_FILENAME,
    load_product_manifest,
)
from book2skill.runtime.profiles import GeneratedSkillProduct, HostRuntime
from book2skill.storage.file_storage import resolve_within

#: Frontmatter block pattern (mirrors :mod:`book2skill.validation.frontmatter_check`).
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<yaml>.*?)\n---\s*(?:\n|$)", re.DOTALL
)

#: Skill name slug pattern (mirrors compiler.ir_builder._SKILL_NAME_PATTERN).
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _ts(dt: _dt.datetime) -> str:
    """Sortable, collision-resistant directory/time stamp."""
    return dt.strftime("%Y%m%dT%H%M%S%f")


def parse_skill_name(skill_dir: Path) -> str:
    """Read the ``name`` field from ``<skill_dir>/SKILL.md`` frontmatter.

    Raises:
        DomainError: With :data:`ErrorCode.INSTALL_SKILL_DIR_INVALID` when the
            directory or SKILL.md is missing, the frontmatter is malformed,
            or the ``name`` field does not match the slug pattern.
    """
    if not skill_dir.is_dir():
        raise DomainError(
            code=ErrorCode.INSTALL_SKILL_DIR_INVALID,
            input_id=str(skill_dir),
            message=f"Skill directory not found: {skill_dir}",
            recovery="Point at a directory produced by `book2skill build`.",
        )
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise DomainError(
            code=ErrorCode.INSTALL_SKILL_DIR_INVALID,
            input_id=str(skill_dir),
            message=f"Skill directory has no SKILL.md: {skill_dir}",
            recovery="Run `book2skill build` to produce a Skill directory.",
        )

    text = skill_md.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise DomainError(
            code=ErrorCode.INSTALL_SKILL_DIR_INVALID,
            input_id=str(skill_dir),
            message="SKILL.md has no YAML frontmatter block.",
            recovery="Rebuild the Skill; the compiler must emit frontmatter.",
        )

    try:
        data = yaml.safe_load(match.group("yaml")) or {}
    except yaml.YAMLError as exc:
        raise DomainError(
            code=ErrorCode.INSTALL_SKILL_DIR_INVALID,
            input_id=str(skill_dir),
            message=f"SKILL.md frontmatter is malformed: {exc}",
            recovery="Rebuild the Skill or fix the frontmatter manually.",
        ) from exc

    if not isinstance(data, dict):
        raise DomainError(
            code=ErrorCode.INSTALL_SKILL_DIR_INVALID,
            input_id=str(skill_dir),
            message="SKILL.md frontmatter must be a YAML mapping at the top level.",
            recovery="Rebuild the Skill; the compiler must emit a mapping.",
        )

    raw_name: object = data.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise DomainError(
            code=ErrorCode.INSTALL_SKILL_DIR_INVALID,
            input_id=str(skill_dir),
            message="SKILL.md frontmatter is missing the 'name' field.",
            recovery="Rebuild the Skill with a valid --name slug.",
        )
    name: str = raw_name
    if not _SKILL_NAME_PATTERN.match(name):
        raise DomainError(
            code=ErrorCode.INSTALL_SKILL_DIR_INVALID,
            input_id=str(skill_dir),
            message=(
                f"SKILL.md 'name'='{name}' does not match the slug pattern "
                "^[a-z0-9]+(?:-[a-z0-9]+)*$."
            ),
            recovery="Use a lowercase hyphenated slug for --name.",
        )
    return name


@dataclass(frozen=True)
class InstallRecord:
    """Outcome of an install or uninstall, returned to the CLI / use case.

    Attributes:
        skill_name: Skill slug resolved from frontmatter (install) or passed
            explicitly (uninstall).
        target_dir: Install slot path (where the Skill ends up, or was removed
            from).
        action: ``"install"`` | ``"uninstall"``.
        backup_path: Where the previous installation was moved; ``None`` when
            no prior install existed, or when ``no_backup=True``.
        files_copied: Number of files copied (0 for dry-run / uninstall).
        dry_run: True when no filesystem mutation actually occurred.
    """

    skill_name: str
    target_dir: Path
    action: str
    backup_path: Path | None = None
    files_copied: int = 0
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)


class HostInstaller(ABC):
    """Abstract base for host-specific Skill installers.

    Subclasses set :attr:`host_kind` and implement :meth:`_target_path` to
    pin the install location. Optional :meth:`_apply_overlay` adds
    host-specific files (e.g., Codex ``agents.md``).

    Parameters:
        project_level: When True, install into the current project (``.claude``
            / ``.agents``) instead of the user-level home directory. Not all
            hosts honour this flag; project-only hosts (TRAE) ignore it.
        project_root: Project root for project-level installs. Defaults to
            :func:`pathlib.Path.cwd`.
        backup_root: Root under which timestamped backups live. Defaults to
            ``~/.book2skill/backups`` so backups survive workspace cleanup.
        now: Optional clock for deterministic timestamps in tests.
    """

    host_kind: str = "base"

    def __init__(
        self,
        *,
        project_level: bool = False,
        project_root: Path | None = None,
        backup_root: Path | None = None,
        now: Callable[[], _dt.datetime] | None = None,
    ) -> None:
        self._project_level = project_level
        self._project_root = (project_root or Path.cwd()).resolve()
        self._backup_root = (
            backup_root or (Path.home() / ".book2skill" / "backups")
        ).resolve()
        self._now = now or _now

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install(
        self,
        skill_dir: Path,
        *,
        dry_run: bool = False,
        no_backup: bool = False,
    ) -> InstallRecord:
        """Install a Skill, enforcing the production product gate by default.

        A tree carrying ``runtime-product.json`` is never treated as a legacy
        tree.  The descriptor and its pinned production Closure are validated
        before any host mutation.  Trees without the descriptor retain the
        historical installer path for backwards compatibility.
        """
        skill_dir = Path(skill_dir).resolve()
        descriptor = skill_dir / PRODUCT_MANIFEST_FILENAME
        if descriptor.is_file():
            manifest = load_product_manifest(descriptor)
            if manifest.closure_hash is None:
                raise DomainError(
                    code=ErrorCode.PROFILE_MANIFEST_INVALID,
                    input_id=str(descriptor),
                    message="Runtime product manifest has no pinned Closure hash.",
                    recovery=(
                        "Regenerate the production product descriptor before "
                        "installing it."
                    ),
                )
            record = self.install_runtime_product(
                skill_dir,
                manifest.product,
                HostRuntime(),
                expected_closure_hash=manifest.closure_hash,
                dry_run=dry_run,
                no_backup=no_backup,
            )
            return replace(
                record,
                notes=[*record.notes, "runtime_preflight=default"],
            )
        return self._install_unchecked(
            skill_dir,
            dry_run=dry_run,
            no_backup=no_backup,
        )

    def _install_unchecked(
        self,
        skill_dir: Path,
        *,
        dry_run: bool = False,
        no_backup: bool = False,
    ) -> InstallRecord:
        """Install *skill_dir* into the host's install slot.

        Flow: resolve name → compute target → backup old → stage copy → swap
        → overlay. On failure after the backup was taken, the previous
        version is restored so the slot is never left empty.

        Raises:
            DomainError: With :data:`ErrorCode.INSTALL_SKILL_DIR_INVALID` when
                *skill_dir* is missing or malformed; with
                :data:`ErrorCode.INSTALL_FAILED` on copy/swap failure.
        """
        skill_dir = Path(skill_dir).resolve()
        name = parse_skill_name(skill_dir)
        target = self._target_path(name)

        if dry_run:
            notes = [
                f"DRY-RUN: would install '{name}' to {target}",
            ]
            if target.exists() and not no_backup:
                notes.append(
                    f"DRY-RUN: would back up existing install to "
                    f"{self._backup_path(name)}"
                )
            return InstallRecord(
                skill_name=name,
                target_dir=target,
                action="install",
                dry_run=True,
                notes=notes,
            )

        backup_path: Path | None = None
        try:
            # Take a backup first so the slot is freed for the atomic swap.
            if target.exists():
                if no_backup:
                    shutil.rmtree(target, ignore_errors=False)
                else:
                    backup_path = self._backup_path(name)
                    self._rename(target, backup_path)

            # Stage the new tree in a sibling directory so the swap is atomic.
            staging = target.parent / f".{target.name}.staging-{_ts(self._now())}"
            try:
                files_copied = self._copy_tree(skill_dir, staging)
                self._apply_overlay(staging)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, target)
            except Exception:
                self._cleanup(staging)
                raise
        except Exception as exc:
            if backup_path is not None and backup_path.exists():
                # Restore the previous version so the slot is never empty.
                self._restore_on_failure(target, backup_path)
            raise DomainError(
                code=ErrorCode.INSTALL_FAILED,
                input_id=str(skill_dir),
                message=f"Install to {target} failed: {exc}",
                recovery=(
                    "The previous Skill version was restored from the "
                    f"backup at {backup_path}. Resolve the underlying error "
                    "and re-run."
                ),
            ) from exc

        files_copied = sum(1 for _ in target.rglob("*") if _.is_file())
        return InstallRecord(
            skill_name=name,
            target_dir=target,
            action="install",
            backup_path=backup_path,
            files_copied=files_copied,
        )

    def install_runtime_product(
        self,
        skill_dir: Path,
        product: GeneratedSkillProduct,
        host_runtime: HostRuntime,
        *,
        expected_closure_hash: str,
        dry_run: bool = False,
        no_backup: bool = False,
    ) -> InstallRecord:
        """Install a generated product only after its runtime gates pass.

        The public :meth:`install` method delegates here automatically when a
        product descriptor is present. Direct callers may still use this
        method to supply an explicit HostRuntime snapshot. It validates
        profile dependencies/capabilities and opens a production Runtime
        Closure before any host filesystem mutation occurs.
        """

        product.assert_installable(host_runtime)
        session = RuntimeClosureSession.open(Path(skill_dir).resolve())
        session.assert_task_contract(
            product.task_contract_id,
            product.task_contract_version,
        )
        session.assert_closure_hash(expected_closure_hash)
        session.assert_capabilities(product.capabilities)
        record = self._install_unchecked(
            skill_dir,
            dry_run=dry_run,
            no_backup=no_backup,
        )
        return replace(
            record,
            notes=[
                *record.notes,
                f"runtime_closure_hash={session.closure_hash}",
            ],
        )

    def uninstall(
        self,
        skill_name: str,
        *,
        dry_run: bool = False,
    ) -> InstallRecord:
        """Remove the Skill install directory for *skill_name*.

        Only the install slot is removed; backups, workspace and raw/schema
        trees are untouched. The Skill name must match the slug pattern so the
        uninstall target is deterministic and bounded.

        Raises:
            DomainError: With :data:`ErrorCode.UNINSTALL_FAILED` when the
                target path is invalid or removal fails.
        """
        if not _SKILL_NAME_PATTERN.match(skill_name):
            raise DomainError(
                code=ErrorCode.UNINSTALL_FAILED,
                input_id=skill_name,
                message=(
                    f"Skill name '{skill_name}' does not match the slug "
                    "pattern ^[a-z0-9]+(?:-[a-z0-9]+)*$."
                ),
                recovery="Use the lowercase hyphenated slug from SKILL.md.",
            )

        target = self._target_path(skill_name)

        if dry_run:
            notes = [
                f"DRY-RUN: would uninstall '{skill_name}' from {target}",
            ]
            if not target.exists():
                notes.append("DRY-RUN: target does not exist; nothing to remove.")
            return InstallRecord(
                skill_name=skill_name,
                target_dir=target,
                action="uninstall",
                dry_run=True,
                notes=notes,
            )

        if not target.exists():
            # Idempotent: uninstalling an absent Skill is a no-op success.
            return InstallRecord(
                skill_name=skill_name,
                target_dir=target,
                action="uninstall",
            )

        try:
            shutil.rmtree(target, ignore_errors=False)
        except OSError as exc:
            raise DomainError(
                code=ErrorCode.UNINSTALL_FAILED,
                input_id=str(target),
                message=f"Uninstall of {target} failed: {exc}",
                recovery="Remove the directory manually and re-run.",
            ) from exc

        return InstallRecord(
            skill_name=skill_name,
            target_dir=target,
            action="uninstall",
        )

    # ------------------------------------------------------------------
    # Hooks for subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def _target_path(self, skill_name: str) -> Path:
        """Compute the install slot path for *skill_name*.

        Subclasses must return a path that is stable for a given host/skill
        combination so uninstall can recompute it. Project-level hosts should
        use :meth:`_resolve_project_target` to apply path-traversal guards.
        """

    def _apply_overlay(self, target_dir: Path) -> None:  # noqa: B027
        """Hook for host-specific files (default: no-op).

        Called after the new tree is staged but before the atomic swap, so
        overlay failures roll back to the previous version. Subclasses
        override this to add host-specific files; the base implementation
        intentionally does nothing so hosts without overlays skip it.
        """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_project_target(self, *parts: str) -> Path:
        """Resolve a project-level path under :attr:`_project_root`.

        Always applies :func:`resolve_within` so a malicious slug cannot
        escape the project root.
        """
        return resolve_within(self._project_root, *parts)

    def _backup_path(self, skill_name: str) -> Path:
        """Compute a non-existing backup path for *skill_name* at *now*.

        Mirrors :meth:`Publisher._snapshot_dir` semantics: a uniqueness
        suffix is appended when the base path already exists so repeated
        installs within the same clock tick never collide.
        """
        base = self._backup_root / self.host_kind / skill_name / _ts(self._now())
        if not base.exists():
            return base
        i = 2
        while True:
            cand = base.with_name(base.name + f"-{i}")
            if not cand.exists():
                return cand
            i += 1

    @staticmethod
    def _rename(src: Path, dst: Path) -> None:
        """Rename *src* to *dst*, creating the parent and asserting absence."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise FileExistsError(f"Backup target already exists: {dst}")
        os.replace(src, dst)

    @staticmethod
    def _copy_tree(src: Path, dst: Path) -> int:
        """Copy *src* into *dst* (recursive). Returns file count copied.

        Uses :func:`shutil.copy2` to preserve mtime/mode for auditability.
        """
        dst.mkdir(parents=True, exist_ok=True)
        count = 0
        for entry in src.rglob("*"):
            rel = entry.relative_to(src)
            target = dst / rel
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, target)
                count += 1
        return count

    @staticmethod
    def _cleanup(staging: Path) -> None:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _restore_on_failure(target: Path, backup: Path) -> None:
        """Best-effort restore of *backup* into *target* on failure.

        Never raises: the caller is already in a failure path.
        """
        try:
            if target.exists():
                trash = target.with_name(target.name + ".broken")
                if trash.exists():
                    shutil.rmtree(trash, ignore_errors=True)
                os.replace(target, trash)
                shutil.rmtree(trash, ignore_errors=True)
            os.replace(backup, target)
        except OSError:
            pass


__all__ = [
    "HostInstaller",
    "InstallRecord",
    "parse_skill_name",
]
