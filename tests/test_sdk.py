"""Tests for the public Extension SDK (PRD FR-11).

Downstream extensions are the only consumers of :mod:`book2skill.sdk`; these
tests pin the surface so future Core changes cannot silently break it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import book2skill.sdk as sdk
from book2skill.domain.knowledge import (
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    UnitKind,
)
from book2skill.sdk import (
    ExtensionContext,
    ExtensionManifest,
    SkillCompilerService,
    SourceService,
    StorageService,
)

# ---------------------------------------------------------------------------
# Sample-extension fixture (shared with P3 extension lifecycle tests).
# ---------------------------------------------------------------------------

SAMPLE_MANIFEST = {
    "schema_version": 1,
    "extension_id": "sample-ext",
    "version": "1.0.0",
    "requires": {"book2skill": ">=0.1.0,<1.0.0", "extensions": []},
    "entry_points": ["sample_ext.extension:activate"],
    "contributes": {"commands": ["sample"], "skills": []},
    "permissions": ["read_normalized_sources", "write_extension_data"],
    "checksums_file": "checksums.sha256",
}


def _write_sample_extension(root: Path) -> Path:
    """Materialise a minimal sample-extension package on disk."""
    ext = root / "sample-ext"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / "extension-manifest.json").write_text(
        json.dumps(SAMPLE_MANIFEST), encoding="utf-8"
    )
    (ext / "checksums.sha256").write_text("", encoding="utf-8")
    return ext


# ---------------------------------------------------------------------------
# ExtensionManifest contract
# ---------------------------------------------------------------------------


class TestExtensionManifest:
    def test_valid_manifest_roundtrip(self) -> None:
        manifest = ExtensionManifest.model_validate(SAMPLE_MANIFEST)
        assert manifest.extension_id == "sample-ext"
        assert manifest.book2skill_range() == ">=0.1.0,<1.0.0"
        assert manifest.extension_dependencies() == []

    def test_invalid_extension_id_rejected(self) -> None:
        bad = dict(SAMPLE_MANIFEST, extension_id="Sample Ext")
        with pytest.raises(ValueError):
            ExtensionManifest.model_validate(bad)

    def test_invalid_semver_rejected(self) -> None:
        bad = dict(SAMPLE_MANIFEST, version="v1")
        with pytest.raises(ValueError):
            ExtensionManifest.model_validate(bad)

    def test_entry_points_required(self) -> None:
        bad = dict(SAMPLE_MANIFEST, entry_points=[])
        with pytest.raises(ValueError):
            ExtensionManifest.model_validate(bad)

    def test_from_file(self, tmp_path: Path) -> None:
        ext = _write_sample_extension(tmp_path)
        manifest = ExtensionManifest.from_file(ext / "extension-manifest.json")
        assert manifest.extension_id == "sample-ext"

    def test_extension_dependencies_parsed(self) -> None:
        data = dict(SAMPLE_MANIFEST)
        data["requires"]["extensions"] = [
            {"extension_id": "dd-methods", "version": "1.x"}
        ]
        manifest = ExtensionManifest.model_validate(data)
        assert len(manifest.extension_dependencies()) == 1
        assert manifest.extension_dependencies()[0].extension_id == "dd-methods"


# ---------------------------------------------------------------------------
# ExtensionContext permissions
# ---------------------------------------------------------------------------


class TestExtensionContext:
    def _ctx(self) -> ExtensionContext:
        return ExtensionContext(
            extension_id="demo",
            version="1.0.0",
            data_root=Path("/tmp/demo-data"),
            tmp_dir=Path("/tmp/demo-tmp"),
        )

    def test_granted_permission(self) -> None:
        ctx = self._ctx()
        assert ctx.has_permission("write_extension_data")
        ctx.require_permission("write_extension_data")  # must not raise

    def test_denied_permission_raises(self) -> None:
        ctx = self._ctx()
        assert not ctx.has_permission("network")
        with pytest.raises(PermissionError):
            ctx.require_permission("network")


# ---------------------------------------------------------------------------
# SourceService
# ---------------------------------------------------------------------------


class TestSourceService:
    def test_discover_returns_files_and_errors(self, tmp_path: Path) -> None:
        src = tmp_path / "a.txt"
        src.write_text("hello world", encoding="utf-8")
        missing = tmp_path / "missing.pdf"
        files, errors = SourceService().discover([str(src), str(missing)])
        assert len(files) == 1
        assert files[0].original_name == "a.txt"
        assert len(errors) == 1

    def test_sha256_and_source_id(self, tmp_path: Path) -> None:
        src = tmp_path / "a.txt"
        src.write_text("stable content", encoding="utf-8")
        svc = SourceService()
        digest = svc.sha256(src)
        assert len(digest) == 64
        assert svc.source_id_for(src) == digest[:12]


# ---------------------------------------------------------------------------
# SkillCompilerService (compile + write + validate)
# ---------------------------------------------------------------------------


class TestSkillCompilerService:
    def _unit(self) -> KnowledgeUnit:
        return KnowledgeUnit(
            unit_id="ku-1",
            kind=UnitKind.PRINCIPLE,
            content="Always write tests before shipping code.",
            conditions=[],
            exceptions=[],
            source_refs=[KnowledgeRef(source_id="src-1", block_id="blk-1", quote="q")],
            confidence=0.9,
            review_status=KnowledgeStatus.APPROVED,
        )

    def test_build_write_validate(self, tmp_path: Path) -> None:
        svc = SkillCompilerService(output_dir=tmp_path)
        spec = sdk.SkillSpec(
            name="sdk-demo",
            description="A Skill compiled through the public SDK.",
            use_when=["When you need structured knowledge."],
            do_not_use_when=[],
        )
        ir = svc.build_ir([self._unit()], spec)
        assert ir.name == "sdk-demo"
        svc.validate_ir(ir)  # must pass schema validation
        out = svc.write(ir)
        assert (out / "SKILL.md").exists()


class TestExtractionService:
    def test_extract_text_blocks_txt(self, tmp_path: Path) -> None:
        src = tmp_path / "a.txt"
        src.write_text("# Heading\n\nBody paragraph.", encoding="utf-8")
        from book2skill.sdk import ExtractionService

        blocks = ExtractionService().extract_text_blocks(src)
        assert blocks  # at least one normalized block
        assert any("Heading" in b.text for b in blocks)

    def test_unknown_format_raises(self, tmp_path: Path) -> None:
        src = tmp_path / "a.xyz"
        src.write_bytes(b"\x00\x01\x02")
        from book2skill.sdk import ExtractionService

        with pytest.raises(LookupError):
            ExtractionService().extract_text_blocks(src)


class TestStorageService:
    def test_instantiates_three_storage_backends(self, tmp_path: Path) -> None:
        svc = StorageService(tmp_path)
        # Each facade attribute is a concrete storage port.
        assert svc.raw is not None
        assert svc.schema is not None
        assert svc.wiki is not None


def test_all_public_names_exported() -> None:
    required = [
        "SourceService",
        "ExtractionService",
        "StorageService",
        "SkillCompilerService",
        "ValidatorRegistryService",
        "ExtensionContext",
        "ExtensionManifest",
        "SkillIR",
        "TextBlock",
    ]
    for name in required:
        assert hasattr(sdk, name), f"missing SDK export: {name}"
