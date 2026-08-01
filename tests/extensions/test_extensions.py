"""Tests for the extension lifecycle (PRD FR-11 / B2S-M5-03).

Covers the semver matcher, package inspection/integrity, registry persistence,
dependency resolution, and the full install → upgrade → rollback → uninstall
lifecycle including the "refuse to uninstall a depended-on extension" rule.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from book2skill.extensions import (
    ExtensionError,
    ExtensionManager,
    inspect_package,
)
from book2skill.extensions.package import build_checksums_text, extract_payload
from book2skill.extensions.registry import ExtensionRegistry
from book2skill.extensions.resolver import ResolutionError, install_order
from book2skill.extensions.version import Version, best_match, satisfies_range

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def build_package(
    root: Path,
    ext_id: str,
    version: str,
    *,
    requires: str = ">=0.1.0,<1.0.0",
    deps: list[tuple[str, str]] | None = None,
    permissions: list[str] | None = None,
) -> Path:
    """Materialise an extension package directory + ZIP with valid checksums."""
    d = root / f"{ext_id}-{version}"
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "extension_id": ext_id,
        "version": version,
        "requires": {
            "book2skill": requires,
            "extensions": [
                {"extension_id": x, "version": v} for x, v in (deps or [])
            ],
        },
        "entry_points": ["mod:activate"],
        "contributes": {"commands": [ext_id]},
        "permissions": permissions or ["read_normalized_sources"],
        "checksums_file": "checksums.sha256",
    }
    files = {
        "extension-manifest.json": json.dumps(manifest),
        "mod.py": f"def activate(ctx):\n    return '{ext_id}'  # v{version}\n",
    }
    encoded: dict[str, bytes] = {}
    for rel, data in files.items():
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data.encode("utf-8"))
        encoded[rel] = data.encode("utf-8")
    (d / "checksums.sha256").write_text(
        build_checksums_text(encoded), encoding="utf-8"
    )

    zpath = root / f"{ext_id}-{version}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in d.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(d).as_posix())
    return zpath


@pytest.fixture()
def manager(tmp_path: Path) -> ExtensionManager:
    return ExtensionManager(tmp_path / "registry")


# ---------------------------------------------------------------------------
# Semantic version matching
# ---------------------------------------------------------------------------


class TestVersionRange:
    def test_parse_and_compare(self) -> None:
        assert Version.parse("1.2.3") < Version.parse("1.2.4")
        assert Version.parse("1.2.3-alpha") < Version.parse("1.2.3")
        assert Version.parse("1.2.3") == Version.parse("1.2.3+build")

    def test_satisfies_operator_and_wildcard(self) -> None:
        v = Version.parse("1.4.0")
        assert satisfies_range(v, ">=1.0.0,<2.0.0")
        assert satisfies_range(v, "1.x")
        assert not satisfies_range(v, "2.x")
        assert satisfies_range(Version.parse("1.2.0"), "~=1.2.0")

    def test_best_match_picks_newest(self) -> None:
        assert best_match(["0.1.0", "0.9.0", "1.0.0"], ">=0.1.0,<1.0.0") == "0.9.0"
        assert best_match(["1.0.0"], "2.x") is None


# ---------------------------------------------------------------------------
# Package inspection & integrity
# ---------------------------------------------------------------------------


class TestPackageInspection:
    def test_inspect_zip_backs_manifest(self, tmp_path: Path) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        inspected = inspect_package(zpath)
        assert inspected.extension_id == "alpha"
        assert inspected.version == "1.0.0"
        assert inspected.file_count == 1  # mod.py only

    def test_tampered_checksum_rejected(self, tmp_path: Path) -> None:
        build_package(tmp_path, "alpha", "1.0.0")
        # Rebuild the ZIP with mod.py altered, keeping the ORIGINAL checksums
        # that no longer match the new payload.
        checksums = (tmp_path / "alpha-1.0.0" / "checksums.sha256").read_text(
            encoding="utf-8"
        )
        tampered = tmp_path / "tampered.zip"
        with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("extension-manifest.json", json.dumps({
                "schema_version": 1, "extension_id": "alpha", "version": "1.0.0",
                "requires": {"book2skill": ">=0.1.0", "extensions": []},
                "entry_points": ["mod:activate"], "contributes": {},
                "permissions": [], "checksums_file": "checksums.sha256",
            }))
            zf.writestr("mod.py", "def activate(ctx): tampered = True\n")
            zf.writestr("checksums.sha256", checksums)
        with pytest.raises(ValueError):
            inspect_package(tampered)

    def test_missing_manifest_rejected(self, tmp_path: Path) -> None:
        zpath = tmp_path / "bad.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("mod.py", "x = 1\n")
        with pytest.raises(ValueError):
            inspect_package(zpath)


# ---------------------------------------------------------------------------
# Registry persistence
# ---------------------------------------------------------------------------


class TestRegistry:
    def _extract(self, tmp_path: Path, which: Path, tag: str) -> Path:
        dest = tmp_path / f"staging-{tag}"
        extract_payload(which, dest)
        return dest

    def test_add_version_and_set_active(self, tmp_path: Path) -> None:
        reg = ExtensionRegistry(tmp_path / "registry")
        pkg = build_package(tmp_path, "alpha", "1.0.0")
        mf = inspect_package(pkg).manifest
        staging = self._extract(tmp_path, pkg, "1")
        reg.add_version(mf, staging)
        assert reg.is_installed("alpha")
        assert reg.versions("alpha") == ["1.0.0"]
        # Reload from disk.
        reg2 = ExtensionRegistry(tmp_path / "registry")
        assert reg2.active_version("alpha") == "1.0.0"

    def test_side_by_side_versions(self, tmp_path: Path) -> None:
        reg = ExtensionRegistry(tmp_path / "registry")
        a_pkg = build_package(tmp_path, "alpha", "1.0.0")
        b_pkg = build_package(tmp_path, "alpha", "1.1.0")
        a = inspect_package(a_pkg).manifest
        b = inspect_package(b_pkg).manifest
        reg.add_version(a, self._extract(tmp_path, a_pkg, "1"))
        reg.add_version(b, self._extract(tmp_path, b_pkg, "2"))
        reg.set_active("alpha", "1.0.0")
        assert reg.versions("alpha") == ["1.0.0", "1.1.0"]
        assert reg.active_version("alpha") == "1.0.0"


# ---------------------------------------------------------------------------
# Dependency resolution
# ---------------------------------------------------------------------------


class TestResolver:
    def test_install_order_dependency_first(self, tmp_path: Path) -> None:
        reg = ExtensionRegistry(tmp_path / "registry")
        alpha = inspect_package(build_package(tmp_path, "alpha", "1.0.0")).manifest
        beta = inspect_package(
            build_package(tmp_path, "beta", "1.0.0", deps=[("alpha", "1.x")])
        ).manifest
        assert install_order([alpha, beta], reg) == ["alpha", "beta"]

    def test_missing_dependency_rejected(self, tmp_path: Path) -> None:
        reg = ExtensionRegistry(tmp_path / "registry")
        beta = inspect_package(
            build_package(tmp_path, "beta", "1.0.0", deps=[("missing", "1.x")])
        ).manifest
        with pytest.raises(ResolutionError):
            install_order([beta], reg)

    def test_version_conflict_rejected(self, tmp_path: Path) -> None:
        reg = ExtensionRegistry(tmp_path / "registry")
        alpha = inspect_package(build_package(tmp_path, "alpha", "2.0.0")).manifest
        beta = inspect_package(
            build_package(tmp_path, "beta", "1.0.0", deps=[("alpha", "1.x")])
        ).manifest
        with pytest.raises(ResolutionError):
            install_order([alpha, beta], reg)


# ---------------------------------------------------------------------------
# ExtensionManager full lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_install_doctor_list(
        self, manager: ExtensionManager, tmp_path: Path
    ) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        manager.install(zpath)
        assert manager.registry.is_installed("alpha")
        assert manager.doctor("alpha").ok
        rows = manager.list()
        assert rows[0]["extension_id"] == "alpha"

    def test_install_twice_rejected(
        self, manager: ExtensionManager, tmp_path: Path
    ) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        manager.install(zpath)
        with pytest.raises(ExtensionError):
            manager.install(zpath)

    def test_upgrade_and_rollback(
        self, manager: ExtensionManager, tmp_path: Path
    ) -> None:
        manager.install(build_package(tmp_path, "alpha", "1.0.0"))
        manager.install(
            build_package(tmp_path, "beta", "2.0.0", deps=[("alpha", "1.x")])
        )
        # Upgrade alpha 1.0.0 → 1.1.0.
        manager.upgrade(build_package(tmp_path, "alpha", "1.1.0"))
        assert manager.registry.active_version("alpha") == "1.1.0"
        # Beta still satisfied after upgrade.
        assert manager.doctor("beta").ok
        # Roll alpha back to 1.0.0.
        manager.rollback("alpha")
        assert manager.registry.active_version("alpha") == "1.0.0"

    def test_refuse_uninstall_of_dependency(
        self, manager: ExtensionManager, tmp_path: Path
    ) -> None:
        manager.install(build_package(tmp_path, "alpha", "1.0.0"))
        manager.install(
            build_package(tmp_path, "beta", "2.0.0", deps=[("alpha", "1.x")])
        )
        # Removing a depended-on extension must be refused.
        with pytest.raises(ExtensionError):
            manager.uninstall("alpha")
        assert manager.registry.is_installed("alpha")

    def test_uninstall_leaf(
        self, manager: ExtensionManager, tmp_path: Path
    ) -> None:
        manager.install(build_package(tmp_path, "alpha", "1.0.0"))
        manager.install(
            build_package(tmp_path, "beta", "2.0.0", deps=[("alpha", "1.x")])
        )
        manager.uninstall("beta")  # no downstream dependant
        assert not manager.registry.is_installed("beta")
        # alpha must remain.
        assert manager.registry.is_installed("alpha")

    def test_core_compat_range_enforced(self, tmp_path: Path) -> None:
        manager = ExtensionManager(tmp_path / "registry")
        zpath = build_package(
            tmp_path, "alpha", "1.0.0", requires=">=9.0.0,<10.0.0"
        )
        with pytest.raises(ExtensionError):
            manager.install(zpath)
