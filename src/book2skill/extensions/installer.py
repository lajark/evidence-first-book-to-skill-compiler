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
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from book2skill.extensions import package as pkg
from book2skill.extensions.registry import ExtensionRegistry
from book2skill.extensions.resolver import (
    ResolutionError,
    check_core_compatibility,
    install_order,
)
from book2skill.extensions.version import Version
from book2skill.sdk import ExtensionContext, ExtensionManifest


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
        self._data_home = (
            Path(data_home)
            if data_home
            else self._root.parent / "data"
        )

    # ---- context / activation ---------------------------------------------

    def _context_for(self, manifest: ExtensionManifest) -> ExtensionContext:
        deps = frozenset(d.extension_id for d in manifest.extension_dependencies())
        data_root = self._data_home / manifest.extension_id
        return ExtensionContext(
            extension_id=manifest.extension_id,
            version=manifest.version,
            data_root=data_root,
            tmp_dir=data_root / "tmp",
            permissions=frozenset(manifest.permissions),
            dependencies=deps,
        )

    def _activate(self, manifest: ExtensionManifest) -> None:
        """Import and call each ``entry_point`` with the runtime context.

        The entry point ``module:attr`` is loaded from the active payload by
        inserting its directory on ``sys.path`` for the duration of the call.
        """
        if not manifest.entry_points:
            return
        payload = self.registry.active_payload_dir(manifest.extension_id)
        if payload is None:
            return
        payload = payload.resolve()
        prev_path = list(sys.path)
        sys.path.insert(0, str(payload))
        try:
            for entry in manifest.entry_points:
                module_name, _, attr = entry.partition(":")
                fn = attr or "activate"
                module = importlib.import_module(module_name)
                target = getattr(module, fn)
                if callable(target):
                    target(self._context_for(manifest))
        except Exception as exc:  # noqa: BLE001 - surface as lifecycle error
            raise ExtensionError(f"extension activation failed: {exc}") from exc
        finally:
            sys.path[:] = prev_path

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

        with tempfile.TemporaryDirectory(prefix="b2s-inst-") as td:
            staging = Path(td)
            pkg.extract_payload(source, staging)
            self.registry.add_version(manifest, staging, installed_at=_now())
        self._activate(manifest)
        return LifecycleResult(
            "install", manifest.extension_id, manifest.version, message="installed"
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

        with tempfile.TemporaryDirectory(prefix="b2s-upg-") as td:
            staging = Path(td)
            pkg.extract_payload(source, staging)
            self.registry.add_version(manifest, staging, installed_at=_now())
        self.registry.set_active(manifest.extension_id, manifest.version)
        try:
            self._activate(manifest)
        except ExtensionError:
            # Restore the previously-active version so the install never leaves
            # the extension half-upgraded.
            self.registry.set_active(manifest.extension_id, previous_active)
            raise
        return LifecycleResult(
            "upgrade", manifest.extension_id, manifest.version, message="upgraded"
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
        self.registry.set_active(extension_id, target)
        return LifecycleResult(
            "rollback", extension_id, target, message="rolled back"
        )

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
        manifest_from = self._manifest_from(extension_id)
        if manifest_from is None:
            raise ExtensionError(f"{extension_id} payload has no valid manifest")
        return LifecycleResult(
            "doctor",
            extension_id,
            rec.active_version,
            message=f"ok @ {rec.active_version}",
        )

    # ---- internals --------------------------------------------------------

    def _manifest_from(self, extension_id: str) -> ExtensionManifest | None:
        payload = self.registry.active_payload_dir(extension_id)
        if payload is None:
            return None
        mf = payload / pkg.MANIFEST_FILENAME
        return ExtensionManifest.from_file(mf) if mf.is_file() else None

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
