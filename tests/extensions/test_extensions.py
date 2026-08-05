"""Tests for the extension lifecycle (PRD FR-11 / B2S-M5-03).

Covers the semver matcher, package inspection/integrity, registry persistence,
dependency resolution, and the full install → upgrade → rollback → uninstall
lifecycle including the "refuse to uninstall a depended-on extension" rule.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from book2skill.extensions import (
    ExtensionError,
    ExtensionManager,
    inspect_package,
)
from book2skill.extensions import package as package_module
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
    requires: str = ">=0.1.0,<2.0.0",
    deps: list[tuple[str, str]] | None = None,
    permissions: list[str] | None = None,
    contributes: dict[str, list[str]] | None = None,
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
        "contributes": contributes or {"commands": [ext_id]},
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


def repack_package(directory: Path, archive: Path) -> Path:
    """Rebuild a fixture archive after changing one payload file."""
    encoded = {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file() and p.name != "checksums.sha256"
    }
    (directory / "checksums.sha256").write_text(
        build_checksums_text(encoded), encoding="utf-8"
    )
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in directory.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(directory).as_posix())
    return archive


def replace_zip_entry(zpath: Path, rel: str, data: str | bytes) -> None:
    """Replace one ZIP member without creating a duplicate archive entry."""
    replacement = zpath.with_name(f"{zpath.stem}-rewritten.zip")
    with zipfile.ZipFile(zpath, "r") as source:
        retained = [
            (info, source.read(info))
            for info in source.infolist()
            if info.filename != rel
        ]
    with zipfile.ZipFile(replacement, "w", zipfile.ZIP_DEFLATED) as target:
        for info, original in retained:
            target.writestr(info, original)
        target.writestr(rel, data)
    replacement.replace(zpath)


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

    def test_inspect_directory_backs_manifest(self, tmp_path: Path) -> None:
        build_package(tmp_path, "alpha", "1.0.0")
        inspected = inspect_package(tmp_path / "alpha-1.0.0")
        assert inspected.extension_id == "alpha"
        assert inspected.file_count == 1

    def test_inspect_zip_with_single_wrapper_directory(
        self, tmp_path: Path
    ) -> None:
        flat = build_package(tmp_path, "alpha", "1.0.0")
        wrapped = tmp_path / "wrapped.zip"
        with zipfile.ZipFile(flat) as source, zipfile.ZipFile(
            wrapped, "w", zipfile.ZIP_DEFLATED
        ) as target:
            for info in source.infolist():
                target.writestr(f"alpha-package/{info.filename}", source.read(info))

        inspected = inspect_package(wrapped)
        assert inspected.extension_id == "alpha"
        assert inspected.file_count == 1

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

    @pytest.mark.parametrize(
        "member",
        [
            "/escape.py",
            "C:/escape.py",
            "//server/share/escape.py",
            "../escape.py",
            "pkg/./escape.py",
        ],
    )
    def test_unsafe_zip_member_rejected(
        self, tmp_path: Path, member: str
    ) -> None:
        manifest = {
            "schema_version": 1,
            "extension_id": "alpha",
            "version": "1.0.0",
            "requires": {"book2skill": ">=0.1.0", "extensions": []},
            "entry_points": ["mod:activate"],
            "contributes": {},
            "permissions": [],
            "checksums_file": "checksums.sha256",
        }
        files = {
            "extension-manifest.json": json.dumps(manifest).encode(),
            "mod.py": b"def activate(ctx): pass\n",
            member: b"escaped = True\n",
        }
        zpath = tmp_path / "unsafe.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            for rel, data in files.items():
                zf.writestr(rel, data)
            checksums = "".join(
                f"{package_module.file_sha256(data)}  {rel}\n"
                for rel, data in files.items()
            )
            zf.writestr("checksums.sha256", checksums)

        with pytest.raises(ValueError, match="unsafe package path"):
            inspect_package(zpath)

    def test_nul_member_rejected(self) -> None:
        with pytest.raises(ValueError, match="NUL"):
            package_module._normalize_member_path("mod.py\x00ignored")

    @pytest.mark.parametrize("member", ["CON", "nul.txt", "lib/AUX.py"])
    def test_windows_reserved_member_rejected(self, member: str) -> None:
        with pytest.raises(ValueError, match="unsafe package path"):
            package_module._normalize_member_path(member)

    def test_duplicate_zip_member_rejected(self, tmp_path: Path) -> None:
        manifest = {
            "schema_version": 1,
            "extension_id": "alpha",
            "version": "1.0.0",
            "requires": {"book2skill": ">=0.1.0", "extensions": []},
            "entry_points": ["mod:activate"],
            "contributes": {},
            "permissions": [],
            "checksums_file": "checksums.sha256",
        }
        files = {
            "extension-manifest.json": json.dumps(manifest).encode(),
            "mod.py": b"second = True\n",
        }
        zpath = tmp_path / "duplicate.zip"
        with (
            pytest.warns(UserWarning, match="Duplicate name"),
            zipfile.ZipFile(zpath, "w") as zf,
        ):
            zf.writestr(
                "extension-manifest.json",
                files["extension-manifest.json"],
            )
            zf.writestr("mod.py", b"first = True\n")
            zf.writestr("mod.py", files["mod.py"])
            zf.writestr("checksums.sha256", build_checksums_text(files))

        with pytest.raises(ValueError, match="duplicate package member"):
            inspect_package(zpath)

    @pytest.mark.parametrize("omitted", ["extension-manifest.json", "mod.py"])
    def test_checksum_inventory_requires_every_file(
        self, tmp_path: Path, omitted: str
    ) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        source_dir = tmp_path / "alpha-1.0.0"
        entries = {
            p.relative_to(source_dir).as_posix(): p.read_bytes()
            for p in source_dir.rglob("*")
            if p.is_file() and p.name != "checksums.sha256"
        }
        entries.pop(omitted)
        checksums = build_checksums_text(entries)
        replace_zip_entry(zpath, "checksums.sha256", checksums)

        with pytest.raises(ValueError, match="checksum inventory mismatch"):
            inspect_package(zpath)

    def test_checksum_inventory_rejects_unknown_file(self, tmp_path: Path) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        with zipfile.ZipFile(zpath) as zf:
            existing = zf.read("checksums.sha256").decode("utf-8")
        replace_zip_entry(
            zpath,
            "checksums.sha256",
            existing + f"{'0' * 64}  ghost.py\n",
        )

        with pytest.raises(ValueError, match="checksum inventory mismatch"):
            inspect_package(zpath)

    @pytest.mark.parametrize(
        "checksums",
        [
            "not-a-digest  mod.py\n",
            f"{'0' * 63}  mod.py\n",
            f"{'0' * 64}\n",
        ],
    )
    def test_malformed_checksum_record_rejected(
        self, tmp_path: Path, checksums: str
    ) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        replace_zip_entry(zpath, "checksums.sha256", checksums)

        with pytest.raises(ValueError, match="invalid checksum record"):
            inspect_package(zpath)

    def test_duplicate_checksum_record_rejected(self, tmp_path: Path) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        with zipfile.ZipFile(zpath) as zf:
            existing = zf.read("checksums.sha256").decode("utf-8")
        mod_line = next(
            line for line in existing.splitlines() if line.endswith("  mod.py")
        )
        replace_zip_entry(
            zpath, "checksums.sha256", existing + mod_line + "\n"
        )

        with pytest.raises(ValueError, match="duplicate checksum record"):
            inspect_package(zpath)

    def test_extract_rejects_traversal_before_write(self, tmp_path: Path) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        with zipfile.ZipFile(zpath, "a") as zf:
            zf.writestr("../escape.py", "escaped = True\n")

        target = tmp_path / "staging"
        with pytest.raises(ValueError, match="unsafe package path"):
            extract_payload(zpath, target)
        assert not (tmp_path / "escape.py").exists()

    def test_package_member_budget_enforced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        monkeypatch.setattr(package_module, "MAX_PACKAGE_FILES", 2)

        with pytest.raises(ValueError, match="too many files"):
            inspect_package(zpath)

    @pytest.mark.parametrize(
        ("limit_name", "message"),
        [
            ("MAX_FILE_BYTES", "package file exceeds"),
            ("MAX_TOTAL_BYTES", "package exceeds"),
        ],
    )
    def test_package_size_budget_enforced(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        limit_name: str,
        message: str,
    ) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        monkeypatch.setattr(package_module, limit_name, 1)

        with pytest.raises(ValueError, match=message):
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

    def test_reload_repairs_missing_active_payload(self, tmp_path: Path) -> None:
        reg = ExtensionRegistry(tmp_path / "registry")
        first_pkg = build_package(tmp_path, "alpha", "1.0.0")
        second_pkg = build_package(tmp_path, "alpha", "1.1.0")
        first = inspect_package(first_pkg).manifest
        second = inspect_package(second_pkg).manifest
        reg.add_version(first, self._extract(tmp_path, first_pkg, "1"))
        reg.add_version(second, self._extract(tmp_path, second_pkg, "2"))
        reg.set_active("alpha", "1.0.0")

        shutil.rmtree(reg.payload_dir("alpha", "1.0.0"))
        repaired = ExtensionRegistry(tmp_path / "registry")

        assert repaired.versions("alpha") == ["1.1.0"]
        assert repaired.active_version("alpha") == "1.1.0"

    def test_reload_removes_record_when_all_payloads_are_missing(
        self, tmp_path: Path
    ) -> None:
        reg = ExtensionRegistry(tmp_path / "registry")
        package = build_package(tmp_path, "alpha", "1.0.0")
        manifest = inspect_package(package).manifest
        reg.add_version(manifest, self._extract(tmp_path, package, "1"))

        shutil.rmtree(reg.payload_dir("alpha", "1.0.0"))
        repaired = ExtensionRegistry(tmp_path / "registry")

        assert repaired.is_installed("alpha") is False

    def test_concurrent_registry_writes_do_not_drop_versions(
        self, tmp_path: Path
    ) -> None:
        registry_root = tmp_path / "registry"
        first_pkg = build_package(tmp_path, "alpha", "1.0.0")
        second_pkg = build_package(tmp_path, "alpha", "1.1.0")
        first = inspect_package(first_pkg).manifest
        second = inspect_package(second_pkg).manifest
        first_staging = self._extract(tmp_path, first_pkg, "concurrent-1")
        second_staging = self._extract(tmp_path, second_pkg, "concurrent-2")

        def install(manifest, staging: Path) -> None:
            ExtensionRegistry(registry_root).add_version(manifest, staging)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(
                pool.map(
                    install,
                    (first, second),
                    (first_staging, second_staging),
                )
            )

        repaired = ExtensionRegistry(registry_root)
        assert repaired.versions("alpha") == ["1.0.0", "1.1.0"]


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
    def test_restart_reloads_active_extension_contribution(
        self, tmp_path: Path
    ) -> None:
        registry_root = tmp_path / "registry"
        data_home = tmp_path / "data"
        package = build_package(
            tmp_path,
            "alpha",
            "1.0.0",
            permissions=[
                "register_commands",
                "register_extractors",
                "register_validators",
            ],
            contributes={
                "commands": ["alpha"],
                "extractors": ["alpha-format"],
                "validators": ["alpha-check"],
            },
        )
        source_dir = tmp_path / "alpha-1.0.0"
        (source_dir / "mod.py").write_text(
            "def activate(ctx):\n"
            "    ctx.data_root.mkdir(parents=True, exist_ok=True)\n"
            "    assert ctx.registrar is not None\n"
            "    ctx.registrar.register_command('alpha', lambda: ctx.version)\n"
            "    class Extractor:\n"
            "        def extract(self):\n"
            "            return 'extracted'\n"
            "    class Validator:\n"
            "        def run(self):\n"
            "            return 'validated'\n"
            "    ctx.registrar.register_extractor('alpha-format', Extractor())\n"
            "    ctx.registrar.register_validator('alpha-check', Validator())\n"
            "    (ctx.data_root / 'loaded.txt').write_text(\n"
            "        ctx.version, encoding='utf-8'\n"
            "    )\n",
            encoding="utf-8",
        )
        repack_package(source_dir, package)

        manager = ExtensionManager(registry_root, data_home=data_home)
        manager.install(package)
        marker = data_home / "alpha" / "loaded.txt"
        assert marker.read_text(encoding="utf-8") == "1.0.0"
        assert manager.contributions.commands["alpha"]() == "1.0.0"
        assert (
            manager.contributions.extractors["alpha-format"].extract()
            == "extracted"
        )
        assert (
            manager.contributions.validators["alpha-check"].run()
            == "validated"
        )
        marker.unlink()

        restarted = ExtensionManager(registry_root, data_home=data_home)

        assert marker.read_text(encoding="utf-8") == "1.0.0"
        assert restarted.contributions.commands["alpha"]() == "1.0.0"
        assert (
            restarted.contributions.extractors["alpha-format"].extract()
            == "extracted"
        )
        assert (
            restarted.contributions.validators["alpha-check"].run()
            == "validated"
        )

    def test_restart_refuses_tampered_active_payload_before_activation(
        self, tmp_path: Path
    ) -> None:
        registry_root = tmp_path / "registry"
        data_home = tmp_path / "data"
        package = build_package(tmp_path, "alpha", "1.0.0")
        source_dir = tmp_path / "alpha-1.0.0"
        (source_dir / "mod.py").write_text(
            "def activate(ctx):\n"
            "    ctx.data_root.mkdir(parents=True, exist_ok=True)\n"
            "    (ctx.data_root / 'activated.txt').write_text(\n"
            "        'yes', encoding='utf-8'\n"
            "    )\n",
            encoding="utf-8",
        )
        repack_package(source_dir, package)

        manager = ExtensionManager(registry_root, data_home=data_home)
        manager.install(package)
        marker = data_home / "alpha" / "activated.txt"
        marker.unlink()
        payload = manager.registry.payload_dir("alpha", "1.0.0")
        (payload / "mod.py").write_text(
            "def activate(ctx):\n    raise RuntimeError('must not run')\n",
            encoding="utf-8",
        )

        with pytest.raises(ExtensionError, match="payload integrity failed"):
            ExtensionManager(registry_root, data_home=data_home)

        assert not marker.exists()

    def test_undeclared_permission_blocks_contribution_activation(
        self, tmp_path: Path
    ) -> None:
        registry_root = tmp_path / "registry"
        package = build_package(tmp_path, "alpha", "1.0.0")
        source_dir = tmp_path / "alpha-1.0.0"
        (source_dir / "mod.py").write_text(
            "def activate(ctx):\n"
            "    assert ctx.registrar is not None\n"
            "    ctx.registrar.register_command('alpha', lambda: 'unsafe')\n",
            encoding="utf-8",
        )
        repack_package(source_dir, package)

        manager = ExtensionManager(registry_root)
        with pytest.raises(ExtensionError, match="lacks 'register_commands'"):
            manager.install(package)

        assert manager.registry.is_installed("alpha") is False
        assert manager.contributions.commands == {}

    def test_install_doctor_list(
        self, manager: ExtensionManager, tmp_path: Path
    ) -> None:
        zpath = build_package(tmp_path, "alpha", "1.0.0")
        manager.install(zpath)
        assert manager.registry.is_installed("alpha")
        assert manager.doctor("alpha").ok
        rows = manager.list()
        assert rows[0]["extension_id"] == "alpha"

    def test_doctor_detects_payload_tampering(
        self, manager: ExtensionManager, tmp_path: Path
    ) -> None:
        manager.install(build_package(tmp_path, "alpha", "1.0.0"))
        payload = manager.registry.payload_dir("alpha", "1.0.0")
        (payload / "mod.py").write_text(
            "def activate(ctx):\n    return 'tampered'\n", encoding="utf-8"
        )

        with pytest.raises(ExtensionError, match="payload integrity failed"):
            manager.doctor("alpha")

    def test_activation_failure_rolls_back_and_keeps_durable_journal(
        self, manager: ExtensionManager, tmp_path: Path
    ) -> None:
        manager.install(build_package(tmp_path, "alpha", "1.0.0"))
        failing = build_package(tmp_path, "alpha", "1.1.0")
        source_dir = tmp_path / "alpha-1.1.0"
        (source_dir / "mod.py").write_text(
            "def activate(ctx):\n    raise RuntimeError('activation boom')\n",
            encoding="utf-8",
        )
        repack_package(source_dir, failing)

        with pytest.raises(ExtensionError, match="activation failed"):
            manager.upgrade(failing)

        assert manager.registry.active_version("alpha") == "1.0.0"
        assert manager.registry.versions("alpha") == ["1.0.0"]
        journals = list(
            (tmp_path / "registry" / ".transactions" / "extensions").glob("*.json")
        )
        assert len(journals) == 1
        journal = json.loads(journals[0].read_text(encoding="utf-8"))
        assert journal["state"] == "rolled_back"
        assert [event["state"] for event in journal["events"]] == [
            "prepared",
            "staged",
            "validated",
            "activation_started",
            "active_switched",
            "rollback_required",
            "rolled_back",
        ]

        # A fresh manager must treat the durable rolled-back journal as
        # terminal and preserve the restored active version.
        restarted = ExtensionManager(tmp_path / "registry")
        assert restarted.registry.active_version("alpha") == "1.0.0"

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
