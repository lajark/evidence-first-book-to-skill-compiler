"""Extension lifecycle orchestration.

:class:`ExtensionManager` drives the FR-11 lifecycle against an
:class:`ExtensionRegistry`:

- ``inspect``  — parse + verify a package (no side effects)
- ``install`` — resolve dependencies, stage payload, register, run migrations
- ``doctor``  — re-verify an installed extension's integrity
- ``list``    — report installed extensions and active versions
- ``upgrade`` — side-by-side install of a newer version, switch active atomically
- ``rollback``— switch back to a previously-verified version
- ``uninstall``— refuse when a downstream dependency exists; else remove payload
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from book2skill.extensions import package as pkg
from book2skill.extensions.registry import ExtensionRegistry
from book2skill.extensions.resolver import (
    ResolutionError,
    check_core_compatibility,
    install_order,
)
from book2skill.extensions.version import Version
from book2skill.sdk import (
    ExtensionContext,
    ExtensionContributionRegistry,
    ExtensionManifest,
    ExtensionRegistrar,
)


class ExtensionError(Exception):
    """Raised for any extension lifecycle failure."""


@dataclass
class LifecycleResult:
    """Outcome of one extension operation."""

    operation: str
    extension_id: str
    version: str | None = None
    ok: bool = True
    message: str = ""


class ExtensionManager:
    """Apply the extension lifecycle over a registry rooted at *root*."""

    def __init__(
        self, registry_root: str | Path, *, data_home: str | Path | None = None
    ) -> None:
        self._root = Path(registry_root).resolve()
        self.registry = ExtensionRegistry(self._root)
        self._contributions = ExtensionContributionRegistry()
        self._transactions = self._root / ".transactions" / "extensions"
        self._data_home = (
            Path(data_home)
            if data_home
            else self._root.parent / "data"
        )
        # Finish any interrupted lifecycle before importing a payload.  A
        # restarted process must activate exactly the recovered active version,
        # never a staging or rollback target.
        self._recover_transactions()
        self._activate_active_extensions()

    # ---- context / activation ---------------------------------------------

    @property
    def contributions(self) -> ExtensionContributionRegistry:
        """Core-owned active contributions loaded in this process."""
        return self._contributions

    def _context_for(
        self, manifest: ExtensionManifest, registrar: ExtensionRegistrar
    ) -> ExtensionContext:
        deps = frozenset(d.extension_id for d in manifest.extension_dependencies())
        data_root = self._data_home / manifest.extension_id
        return ExtensionContext(
            extension_id=manifest.extension_id,
            version=manifest.version,
            data_root=data_root,
            tmp_dir=data_root / "tmp",
            permissions=frozenset(manifest.permissions),
            dependencies=deps,
            registrar=registrar,
        )

    def _activate(self, manifest: ExtensionManifest) -> None:
        """Import and call each ``entry_point`` with the runtime context.

        The entry point ``module:attr`` is loaded from the active payload by
        inserting its directory on ``sys.path`` for the duration of the call.
        """
        registrar = self._contributions.registrar_for(
            extension_id=manifest.extension_id,
            version=manifest.version,
            permissions=frozenset(manifest.permissions),
            declared_contributions=manifest.contributes,
        )
        if not manifest.entry_points:
            self._contributions.commit(registrar)
            return
        payload = self.registry.active_payload_dir(manifest.extension_id)
        if payload is None:
            return
        payload = payload.resolve()
        prev_path = list(sys.path)
        previous_dont_write_bytecode = sys.dont_write_bytecode
        sys.path.insert(0, str(payload))
        # An activation callback must not mutate the verified payload by
        # creating ``__pycache__`` entries.  Those runtime artefacts would
        # invalidate the package inventory that doctor checks later.
        sys.dont_write_bytecode = True
        try:
            for entry in manifest.entry_points:
                module_name, _, attr = entry.partition(":")
                fn = attr or "activate"
                previous_module = sys.modules.pop(module_name, None)
                try:
                    module = importlib.import_module(module_name)
                    target = getattr(module, fn)
                    if callable(target):
                        target(self._context_for(manifest, registrar))
                finally:
                    # Never let one version's module object leak into the next
                    # activation.  This is important for upgrades where the
                    # entry-point name is unchanged but the payload changed.
                    sys.modules.pop(module_name, None)
                    if previous_module is not None:
                        sys.modules[module_name] = previous_module
        except Exception as exc:  # noqa: BLE001 - surface as lifecycle error
            raise ExtensionError(f"extension activation failed: {exc}") from exc
        finally:
            sys.path[:] = prev_path
            sys.dont_write_bytecode = previous_dont_write_bytecode
        self._contributions.commit(registrar)

    def _activate_active_extensions(self) -> None:
        """Re-verify and load every active extension after process startup.

        Contributions are process-local Python registrations, so registry
        state alone is insufficient after a restart. Integrity verification is
        deliberately repeated before import: a tampered active payload must
        fail closed rather than run merely because it was valid at install
        time.
        """
        for extension_id in self.registry.installed_ids():
            version = self.registry.active_version(extension_id)
            if version is None:
                continue
            self._verify_payload(extension_id, version)
            manifest = self._manifest_from_version(extension_id, version)
            if manifest is None:
                raise ExtensionError(
                    f"{extension_id} version {version} has no valid manifest"
                )
            self._activate(manifest)

    # ---- operations -------------------------------------------------------

    def inspect(self, source: str | Path) -> pkg.InspectedExtension:
        """Parse and integrity-verify an extension package (no install)."""
        return pkg.inspect_package(source)

    def list(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for ext_id in self.registry.installed_ids():
            rec = self.registry.record(ext_id)
            if rec is None:
                continue
            rows.append(
                {
                    "extension_id": ext_id,
                    "versions": rec.versions,
                    "active_version": rec.active_version,
                }
            )
        return rows

    def install(self, source: str | Path) -> LifecycleResult:
        inspected = pkg.inspect_package(source)
        manifest = inspected.manifest
        if self.registry.is_installed(manifest.extension_id):
            raise ExtensionError(
                f"{manifest.extension_id} is already installed; use upgrade"
            )
        try:
            check_core_compatibility(manifest.book2skill_range())
            install_order([manifest], self.registry)
        except ResolutionError as exc:
            raise ExtensionError(str(exc)) from exc

        return self._install_transaction(
            source, manifest, operation="install", previous_active=None
        )

    def upgrade(self, source: str | Path, *, force: bool = False) -> LifecycleResult:
        inspected = pkg.inspect_package(source)
        manifest = inspected.manifest
        if not self.registry.is_installed(manifest.extension_id):
            raise ExtensionError(
                f"{manifest.extension_id} is not installed; use install"
            )
        previous_active = self.registry.active_version(manifest.extension_id)
        assert previous_active is not None
        if previous_active == manifest.version and not force:
            raise ExtensionError(
                f"{manifest.extension_id} version {manifest.version} is already active"
            )
        try:
            check_core_compatibility(manifest.book2skill_range())
            install_order([manifest], self.registry)
        except ResolutionError as exc:
            raise ExtensionError(str(exc)) from exc

        return self._install_transaction(
            source,
            manifest,
            operation="upgrade",
            previous_active=previous_active,
        )

    def rollback(self, extension_id: str) -> LifecycleResult:
        versions = self.registry.versions(extension_id)
        if not versions:
            raise ExtensionError(f"{extension_id} is not installed")
        active = self.registry.active_version(extension_id)
        candidates = sorted((Version.parse(v) for v in versions), reverse=True)
        target = next((str(v) for v in candidates if str(v) != active), None)
        if target is None or target == active:
            raise ExtensionError(f"{extension_id} has no other version to roll back to")
        target_manifest = self._manifest_from_version(extension_id, target)
        if target_manifest is None:
            raise ExtensionError(
                f"{extension_id} version {target} has no valid manifest"
            )
        self._verify_payload(extension_id, target)
        tx = self._begin_transaction(
            "rollback",
            extension_id,
            target,
            previous_active=active,
        )
        try:
            with self.registry.mutation():
                self._write_transaction(tx, "validated")
                self._write_transaction(tx, "activation_started")
                self.registry._set_active_unlocked(extension_id, target)
                self._write_transaction(tx, "active_switched")
                self._activate(target_manifest)
                self._write_transaction(tx, "activated")
                self._write_transaction(tx, "committed")
            self._finish_transaction(tx)
        except Exception as exc:  # noqa: BLE001 - preserve lifecycle context
            self._fail_and_rollback(tx, exc)
            raise
        return LifecycleResult("rollback", extension_id, target, message="rolled back")

    def uninstall(self, extension_id: str) -> LifecycleResult:
        if not self.registry.is_installed(extension_id):
            raise ExtensionError(f"{extension_id} is not installed")
        if self._has_dependants(extension_id):
            raise ExtensionError(
                f"cannot uninstall {extension_id}: another installed extension "
                f"depends on it"
            )
        self.registry.remove(extension_id)
        return LifecycleResult("uninstall", extension_id, message="removed")

    def doctor(self, extension_id: str) -> LifecycleResult:
        rec = self.registry.record(extension_id)
        if rec is None:
            raise ExtensionError(f"{extension_id} is not installed")
        for version in rec.versions:
            self._verify_payload(extension_id, version)
        return LifecycleResult(
            "doctor",
            extension_id,
            rec.active_version,
            message=f"ok @ {rec.active_version}",
        )

    # ---- internals --------------------------------------------------------

    def _install_transaction(
        self,
        source: str | Path,
        manifest: ExtensionManifest,
        *,
        operation: str,
        previous_active: str | None,
    ) -> LifecycleResult:
        previous = self.registry.record(manifest.extension_id)
        previous_record = previous.to_dict() if previous is not None else None
        tx = self._begin_transaction(
            operation,
            manifest.extension_id,
            manifest.version,
            previous_active=previous_active,
            previous_record=previous_record,
        )
        try:
            with tempfile.TemporaryDirectory(prefix=f"b2s-{operation}-") as td:
                staging = Path(td)
                pkg.extract_payload(source, staging)
                self._write_transaction(tx, "staged")
                # Re-read the extracted tree so validation covers exactly what
                # will be copied into the registry, not only the source archive.
                staged_manifest = pkg.inspect_package(staging).manifest
                if staged_manifest != manifest:
                    raise ExtensionError("staged extension manifest changed")
                self._write_transaction(tx, "validated")
                with self.registry.mutation():
                    self._backup_replaced_payload(tx)
                    self._write_transaction(tx, "activation_started")
                    self.registry._add_version_unlocked(
                        manifest, staging, installed_at=_now()
                    )
                    self.registry._set_active_unlocked(
                        manifest.extension_id, manifest.version
                    )
                    self._write_transaction(tx, "active_switched")
                    self._activate(manifest)
                    self._write_transaction(tx, "activated")
                    self._write_transaction(tx, "committed")
            self._finish_transaction(tx)
        except Exception as exc:  # noqa: BLE001 - persist rollback before surfacing
            self._fail_and_rollback(tx, exc)
            raise
        return LifecycleResult(
            operation,
            manifest.extension_id,
            manifest.version,
            message="installed" if operation == "install" else "upgraded",
        )

    def _begin_transaction(
        self,
        operation: str,
        extension_id: str,
        target_version: str,
        *,
        previous_active: str | None,
        previous_record: dict[str, object] | None = None,
    ) -> dict[str, object]:
        tx_id = f"{operation}-{uuid4().hex}"
        tx: dict[str, object] = {
            "transaction_id": tx_id,
            "operation": operation,
            "extension_id": extension_id,
            "target_version": target_version,
            "previous_active": previous_active,
            "previous_record": previous_record,
            "created_at": _now(),
            "updated_at": _now(),
            "state": "prepared",
            "events": [],
            "backup_relative": None,
        }
        self._write_transaction(tx, "prepared")
        return tx

    def _transaction_path(self, tx: dict[str, object]) -> Path:
        self._transactions.mkdir(parents=True, exist_ok=True)
        return self._transactions / f"{tx['transaction_id']}.json"

    def _write_transaction(
        self, tx: dict[str, object], state: str, **extra: object
    ) -> None:
        tx["state"] = state
        tx["updated_at"] = _now()
        tx.update(extra)
        events = tx.setdefault("events", [])
        if isinstance(events, list):
            events.append({"state": state, "at": tx["updated_at"]})
        _atomic_json_write(self._transaction_path(tx), tx)

    def _finish_transaction(self, tx: dict[str, object]) -> None:
        self._cleanup_transaction_backup(tx)
        with suppress(FileNotFoundError):
            self._transaction_path(tx).unlink()

    def _cleanup_transaction_backup(self, tx: dict[str, object]) -> None:
        backup = self._backup_path(tx)
        if backup is not None and backup.exists():
            shutil.rmtree(backup)

    def _backup_path(self, tx: dict[str, object]) -> Path | None:
        value = tx.get("backup_relative")
        if not isinstance(value, str) or not value:
            return None
        return self._root / value

    def _backup_replaced_payload(self, tx: dict[str, object]) -> None:
        extension_id = str(tx["extension_id"])
        version = str(tx["target_version"])
        current = self.registry.payload_dir(extension_id, version)
        if not current.exists():
            return
        backup = self._transactions / str(tx["transaction_id"]) / "backup"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(current, backup)
        tx["backup_relative"] = backup.relative_to(self._root).as_posix()
        _atomic_json_write(self._transaction_path(tx), tx)

    def _fail_and_rollback(self, tx: dict[str, object], cause: Exception) -> None:
        self._write_transaction(tx, "rollback_required", error=str(cause))
        try:
            with self.registry.mutation():
                self._rollback_unlocked(tx)
                self._write_transaction(tx, "rolled_back")
            self._cleanup_transaction_backup(tx)
        except Exception as rollback_exc:  # noqa: BLE001 - retain durable journal
            self._write_transaction(
                tx,
                "rollback_required",
                rollback_error=str(rollback_exc),
            )

    def _rollback_unlocked(self, tx: dict[str, object]) -> None:
        extension_id = str(tx["extension_id"])
        version = str(tx["target_version"])
        previous = tx.get("previous_record")
        backup = self._backup_path(tx)
        target = self.registry.payload_dir(extension_id, version)
        if backup is not None and backup.exists():
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(backup, target)
        elif isinstance(previous, dict):
            old_versions = previous.get("versions", [])
            if version not in old_versions and target.exists():
                shutil.rmtree(target)
        else:
            if target.exists():
                shutil.rmtree(target)
        self.registry._restore_record_unlocked(
            extension_id,
            previous if isinstance(previous, dict) else None,
        )

    def _recover_transactions(self) -> None:
        if not self._transactions.is_dir():
            return
        for path in sorted(self._transactions.glob("*.json")):
            try:
                tx = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(tx, dict):
                    continue
                state = tx.get("state")
                if state == "committed":
                    self._finish_transaction(tx)
                    continue
                if state == "rolled_back":
                    self._cleanup_transaction_backup(tx)
                    continue
                if state == "activated":
                    # The callback succeeded; only journal cleanup was left.
                    self._finish_transaction(tx)
                    continue
                with self.registry.mutation():
                    self._rollback_unlocked(tx)
                    self._write_transaction(tx, "rolled_back")
            except Exception as exc:  # noqa: BLE001 - fail closed for doctor
                with suppress(Exception):
                    tx["state"] = "rollback_required"
                    tx["rollback_error"] = str(exc)
                    _atomic_json_write(path, tx)

    def _manifest_from(self, extension_id: str) -> ExtensionManifest | None:
        payload = self.registry.active_payload_dir(extension_id)
        if payload is None:
            return None
        mf = payload / pkg.MANIFEST_FILENAME
        return ExtensionManifest.from_file(mf) if mf.is_file() else None

    def _manifest_from_version(
        self, extension_id: str, version: str
    ) -> ExtensionManifest | None:
        payload = self.registry.payload_dir(extension_id, version)
        mf = payload / pkg.MANIFEST_FILENAME
        return ExtensionManifest.from_file(mf) if mf.is_file() else None

    def _verify_payload(self, extension_id: str, version: str) -> None:
        rec = self.registry.record(extension_id)
        if rec is None or version not in rec.versions:
            raise ExtensionError(f"{extension_id} version {version} is not installed")
        payload = self.registry.payload_dir(extension_id, version)
        try:
            manifest = pkg.inspect_package(payload).manifest
        except Exception as exc:  # noqa: BLE001 - doctor must fail closed
            raise ExtensionError(
                f"{extension_id} version {version} payload integrity failed: {exc}"
            ) from exc
        if manifest.extension_id != extension_id or manifest.version != version:
            raise ExtensionError(
                f"{extension_id} version {version} payload manifest mismatch"
            )
        expected = rec.payload_hashes.get(version)
        actual = _payload_hashes(payload)
        if not expected:
            raise ExtensionError(
                f"{extension_id} version {version} has no recorded payload "
                "hash inventory"
            )
        if expected != actual:
            missing = sorted(set(expected) - set(actual))
            changed = sorted(
                path
                for path in set(expected) & set(actual)
                if expected[path] != actual[path]
            )
            extra = sorted(set(actual) - set(expected))
            details = "; ".join(
                part
                for part in (
                    f"missing={missing}" if missing else "",
                    f"changed={changed}" if changed else "",
                    f"extra={extra}" if extra else "",
                )
                if part
            )
            raise ExtensionError(
                f"{extension_id} version {version} payload hash mismatch ({details})"
            )

    def _has_dependants(self, extension_id: str) -> bool:
        for other in self.registry.installed_ids():
            if other == extension_id:
                continue
            mf = self._manifest_from(other)
            if mf is not None and any(
                d.extension_id == extension_id for d in mf.extension_dependencies()
            ):
                return True
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(path)


def _payload_hashes(root: Path) -> dict[str, str]:
    import hashlib

    hashes: dict[str, str] = {}
    if not root.is_dir():
        return hashes
    for path in root.rglob("*"):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            hashes[path.relative_to(root).as_posix()] = digest
    return hashes
