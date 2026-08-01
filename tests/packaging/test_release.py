"""Tests for the Core release package (PRD FR-12 / B2S-M5-04).

Assembles a ``book2skill-core-<version>.zip`` and verifies: the outer hash
sidecar, schema-conformant ``release-manifest.json``, a ``checksums.sha256``
that round-trips, and the documented top-level structure.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from book2skill.packaging import build_release
from book2skill.packaging.sample_extension import SAMPLE_EXTENSION_ID


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The actual repository root (where schemas/, skills/, docs live)."""
    return Path(__file__).resolve().parents[2]


def _release(repo_root: Path, tmp_path: Path, **kwargs) -> tuple[Path, Path]:
    dest = tmp_path / "out"
    zip_path = build_release(repo_root=repo_root, dest_dir=dest, **kwargs)
    return zip_path, dest


def _zip_members(zf: zipfile.ZipFile) -> list[str]:
    return zf.namelist()


class TestReleasePackage:
    def test_produces_zip_and_sha256(self, repo_root: Path, tmp_path: Path) -> None:
        zip_path, dest = _release(repo_root, tmp_path)
        assert zip_path.name.startswith("book2skill-core-")
        assert zip_path.exists() and zip_path.stat().st_size > 0
        # The outer .sha256 matches the ZIP digest and lists the filename.
        sidecar = dest / (zip_path.name + ".sha256")
        assert sidecar.exists()
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        assert sidecar.read_text(encoding="utf-8").strip() == digest

    def test_structure(self, repo_root: Path, tmp_path: Path) -> None:
        zip_path, _ = _release(repo_root, tmp_path, skill_dir="skills/book2skill")
        with zipfile.ZipFile(zip_path) as zf:
            names = set(_zip_members(zf))
            for required in (
                "VERSION",
                "release-manifest.json",
                "checksums.sha256",
                "install.py",
                "README_INSTALL.md",
                "contracts/extension-manifest.schema.json",
                "contracts/release-manifest.schema.json",
                "sdk/api-surface.json",
                "examples/sample-extension/extension-manifest.json",
                "skills/book2skill-skill.zip",
            ):
                assert required in names, f"missing {required}"

    def test_manifest_schema_conformant(self, repo_root: Path, tmp_path: Path) -> None:
        zip_path, _ = _release(repo_root, tmp_path)
        with zipfile.ZipFile(zip_path) as zf:
            manifest = json.loads(zf.read("release-manifest.json").decode("utf-8"))
        assert manifest["schema_version"] == 1
        assert manifest["product"] == "book2skill-core"
        assert manifest["extension_schema_versions"] == [1]
        # Every artifact lists a path + 64-hex sha256.
        for art in manifest["artifacts"]:
            assert art["path"] and len(art["sha256"]) == 64
        # The manifest and checksums are computed after artifact hashing, so
        # they are not self-referenced; the stable payload is fully listed.
        paths = {a["path"] for a in manifest["artifacts"]}
        assert "VERSION" in paths
        assert "contracts/source-manifest.schema.json" in paths

    def test_checksums_roundtrip(self, repo_root: Path, tmp_path: Path) -> None:
        zip_path, _ = _release(repo_root, tmp_path)
        with zipfile.ZipFile(zip_path) as zf:
            checksums_text = zf.read("checksums.sha256").decode("utf-8")
            expected: dict[str, str] = {}
            for line in checksums_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                digest, rel = line.split(None, 1)
                expected[rel] = digest
            # checksums.sha256 must not list itself.
            assert "checksums.sha256" not in expected
            # Every other payload member matches its recorded digest.
            for name in _zip_members(zf):
                if name == "checksums.sha256":
                    continue
                digest = hashlib.sha256(zf.read(name)).hexdigest()
                assert expected[name] == digest, f"checksum mismatch for {name}"

    def test_sample_extension_is_valid_package(
        self, repo_root: Path, tmp_path: Path
    ) -> None:
        zip_path, _ = _release(repo_root, tmp_path)
        with zipfile.ZipFile(zip_path) as zf:
            data = zf.read(
                "examples/sample-extension/extension-manifest.json"
            ).decode("utf-8")
        manifest = json.loads(data)
        assert manifest["extension_id"] == SAMPLE_EXTENSION_ID
        assert manifest["version"] == "1.0.0"

    def test_wheel_included_when_provided(
        self, repo_root: Path, tmp_path: Path
    ) -> None:
        wheel = tmp_path / "book2skill-0.1.0-py3-none-any.whl"
        wheel.write_bytes(b"fake-wheel")
        zip_path, _ = _release(repo_root, tmp_path, wheel=wheel)
        with zipfile.ZipFile(zip_path) as zf:
            assert "dist/book2skill-0.1.0-py3-none-any.whl" in _zip_members(zf)
