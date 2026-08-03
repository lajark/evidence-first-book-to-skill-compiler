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
    ContributionRegistrationError,
    ExtensionContext,
    ExtensionContributionRegistry,
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

    def test_schema_version_and_checksum_filename_are_fixed(self) -> None:
        with pytest.raises(ValueError):
            ExtensionManifest.model_validate(
                dict(SAMPLE_MANIFEST, schema_version=2)
            )
        with pytest.raises(ValueError):
            ExtensionManifest.model_validate(
                dict(SAMPLE_MANIFEST, checksums_file="other.sha256")
            )

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

    def test_unknown_fields_and_malformed_dependencies_are_rejected(self) -> None:
        unknown = dict(SAMPLE_MANIFEST, unexpected=True)
        malformed = {
            **SAMPLE_MANIFEST,
            "requires": {
                "book2skill": ">=0.1.0",
                "extensions": [
                    {"extension_id": "other", "version": "1.x", "extra": True}
                ],
            },
        }

        with pytest.raises(ValueError):
            ExtensionManifest.model_validate(unknown)
        with pytest.raises(ValueError):
            ExtensionManifest.model_validate(malformed)


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


class TestExtensionRegistrar:
    def test_declared_permissions_are_enforced_at_core_boundary(self) -> None:
        registry = ExtensionContributionRegistry()
        registrar = registry.registrar_for(
            extension_id="demo",
            version="1.0.0",
            permissions=frozenset({"register_commands"}),
            declared_contributions={"commands": ["demo-command"]},
        )
        def handler() -> str:
            return "ok"

        registrar.register_command("demo-command", handler)
        registry.commit(registrar)

        assert registry.commands["demo-command"]() == "ok"
        with pytest.raises(ContributionRegistrationError):
            registrar.register_command("undeclared", handler)

    def test_extractor_and_validator_require_specific_permissions(self) -> None:
        registry = ExtensionContributionRegistry()
        registrar = registry.registrar_for(
            extension_id="demo",
            version="1.0.0",
            permissions=frozenset(),
            declared_contributions={
                "extractors": ["txt"],
                "validators": ["demo-check"],
            },
        )

        with pytest.raises(ContributionRegistrationError):
            registrar.register_extractor("txt", object())
        with pytest.raises(ContributionRegistrationError):
            registrar.register_validator("demo-check", object())

    def test_declared_extractor_and_validator_are_committed(self) -> None:
        class DemoExtractor:
            def extract(self) -> None:
                return None

        class DemoValidator:
            def run(self) -> None:
                return None

        registry = ExtensionContributionRegistry()
        registrar = registry.registrar_for(
            extension_id="demo",
            version="1.0.0",
            permissions=frozenset({"register_extractors", "register_validators"}),
            declared_contributions={
                "extractors": ["txt"],
                "validators": ["demo-check"],
            },
        )
        extractor = DemoExtractor()
        validator = DemoValidator()
        registrar.register_extractor("txt", extractor)
        registrar.register_validator("demo-check", validator)
        registry.commit(registrar)

        assert registry.extractors["txt"] is extractor
        assert registry.validators["demo-check"] is validator


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

    def test_compile_writes_core_derived_references_and_wiki(
        self, tmp_path: Path
    ) -> None:
        svc = SkillCompilerService(output_dir=tmp_path)
        spec = sdk.SkillSpec(
            name="sdk-demo",
            description="A Skill compiled through the public SDK.",
            use_when=["When you need structured knowledge."],
            do_not_use_when=[],
        )
        technique = self._unit().model_copy(
            update={"unit_id": "ku-2", "kind": UnitKind.TECHNIQUE}
        )

        out = svc.compile([self._unit(), technique], spec)

        assert (out / "references" / "techniques.md").exists()
        assert (out / "references" / "provenance.md").exists()
        assert (out / "wiki" / "chapters.md").exists()
        assert (out / "wiki" / "cheatsheet.md").exists()


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
        "SDK_VERSION",
        "SourceService",
        "ExtractionService",
        "StorageService",
        "SkillCompilerService",
        "ValidatorRegistryService",
        "ExtensionContext",
        "ExtensionContributionRegistry",
        "ExtensionRegistrar",
        "ContributionRegistrationError",
        "ExtensionManifest",
        "SkillIR",
        "TextBlock",
        "KnowledgeStatus",
    ]
    for name in required:
        assert hasattr(sdk, name), f"missing SDK export: {name}"
