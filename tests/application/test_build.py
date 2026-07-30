"""Tests for the Build use cases (TASK-013, PRD FR-03-2 / FR-03-3).

Covers:

- Full Build (``build_from_sources``): end-to-end from txt sources through
  the Analyze pipeline to a Skill directory on disk.
- Build from Analysis (``build_from_bundle``): loading an AnalysisBundle
  JSON file and running the compile tail.
- SkillWriter provenance enrichment when manifests are available.
- CLI ``build`` command via Typer's CliRunner.

The tests use the same in-memory AnalyzeUseCase defaults (Mock LLM, no
data_home) so they run hermetically without fixtures.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from book2skill.application.build import BuildResult, BuildUseCase
from book2skill.application.models import (
    AnalysisBundle,
    CandidateUnit,
    StructureEntry,
)
from book2skill.cli import app
from book2skill.compiler import SkillSpec
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.domain.models import Confidentiality, SourceFormat, SourceManifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_txt(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _spec(
    *,
    name: str = "test-skill",
    description: str = (
        "A test skill that compiles knowledge into a usable, auditable form."
    ),
    use_when: list[str] | None = None,
    no_use_when: list[str] | None = None,
) -> SkillSpec:
    return SkillSpec(
        name=name,
        description=description,
        use_when=use_when or ["You need structured knowledge from a document."],
        do_not_use_when=no_use_when or ["The source is not legally held."],
    )


def _manifest(
    *,
    source_id: str = "abc123def456",
    sha256: str = "a" * 64,
    original_name: str = "book.pdf",
    fmt: SourceFormat = SourceFormat.TXT,
) -> SourceManifest:
    return SourceManifest(
        source_id=source_id,
        version=1,
        original_name=original_name,
        content_sha256=sha256,
        format=fmt,
        rights_confirmed=True,
        ingested_at=datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc),
        extractor="TextExtractor",
        extractor_version="1.0",
        confidentiality=Confidentiality.PERSONAL,
    )


def _bundle(
    *,
    candidate_units: list[CandidateUnit] | None = None,
    source_ids: list[str] | None = None,
    collection_id: str = "col-test",
) -> AnalysisBundle:
    if candidate_units is None:
        candidate_units = [
            CandidateUnit(
                unit_id="cu-1",
                kind="principle",
                content="Always validate input before processing it further.",
                source_refs=[{"source_id": "src1", "block_id": "src1-1"}],
                confidence=0.8,
                review_status="candidate",
                record_version=1,
            ),
            CandidateUnit(
                unit_id="cu-2",
                kind="technique",
                content="Use a hash-based deduplication step to detect conflicts.",
                source_refs=[{"source_id": "src1", "block_id": "src1-2"}],
                confidence=0.7,
                review_status="candidate",
                record_version=1,
            ),
        ]
    return AnalysisBundle(
        collection_id=collection_id,
        source_ids=source_ids or ["src1"],
        structure=[
            StructureEntry(
                block_id="src1-1",
                locator={"kind": "paragraph", "paragraph": 1},
                heading="Intro",
                level=1,
                text_preview="Intro",
            )
        ],
        candidate_units=candidate_units,
        review_queue=[],
    )


# ---------------------------------------------------------------------------
# Full Build (build_from_sources)
# ---------------------------------------------------------------------------


class TestBuildFromSources:
    """End-to-end Full Build tests."""

    def test_single_txt_produces_skill_directory(self, tmp_path: Path) -> None:
        f = _write_txt(
            tmp_path / "book.txt",
            "# Introduction\n\nYou should always validate input carefully.\n\n"
            "Apply this technique when processing user data.",
        )
        use_case = BuildUseCase()
        result = use_case.build_from_sources(
            [str(f)], _spec(), output_dir=tmp_path / "out"
        )
        assert result.skill_dir is not None
        assert (result.skill_dir / "SKILL.md").exists()
        assert (result.skill_dir / "references").is_dir()
        assert (result.skill_dir / "assets").is_dir()
        assert (result.skill_dir / "provenance.yml").exists()
        assert (result.skill_dir / "quality-report.md").exists()

    def test_skill_md_contains_spec_fields(self, tmp_path: Path) -> None:
        f = _write_txt(
            tmp_path / "book.txt",
            "You should always validate input carefully before processing.",
        )
        use_case = BuildUseCase()
        result = use_case.build_from_sources(
            [str(f)],
            _spec(
                name="my-skill",
                description="Compiles validation principles into a usable skill.",
                use_when=["When validating user input."],
                no_use_when=["When input is already trusted."],
            ),
            output_dir=tmp_path / "out",
        )
        assert result.skill_dir is not None
        content = (result.skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "name: my-skill" in content
        assert "Compiles validation principles" in content
        assert "- When validating user input." in content
        assert "- When input is already trusted." in content

    def test_references_generated_for_detail_kinds(self, tmp_path: Path) -> None:
        """technique/case/term kinds sink into references/<kind>.md."""
        # The mock adapter assigns 'technique' to most blocks; ensure at least
        # one technique reference file is produced.
        f = _write_txt(
            tmp_path / "book.txt",
            "Apply this method when handling input: step one then step two.",
        )
        use_case = BuildUseCase()
        result = use_case.build_from_sources(
            [str(f)], _spec(), output_dir=tmp_path / "out"
        )
        assert result.skill_dir is not None
        refs_dir = result.skill_dir / "references"
        # At least one .md file should exist under references/.
        md_files = list(refs_dir.glob("*.md"))
        assert md_files, f"expected references/*.md, got {md_files}"

    def test_knowledge_units_persisted_to_units_jsonl(
        self, tmp_path: Path
    ) -> None:
        """With data_home set, units.jsonl is written under schema/<coll>/."""
        f = _write_txt(
            tmp_path / "book.txt",
            "You should always validate input carefully before processing.",
        )
        data_home = tmp_path / "data"
        use_case = BuildUseCase(data_home=data_home)
        result = use_case.build_from_sources(
            [str(f)], _spec(), output_dir=tmp_path / "out"
        )
        assert result.collection_id is not None
        units_path = (
            data_home / "schema" / result.collection_id / "units.jsonl"
        )
        assert units_path.exists()
        lines = [
            ln
            for ln in units_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert len(lines) >= 1
        # Each line is a valid KnowledgeUnit JSON with unit_id and kind.
        for ln in lines:
            obj = json.loads(ln)
            assert "unit_id" in obj
            assert "kind" in obj

    def test_provenance_enriched_with_real_manifest(self, tmp_path: Path) -> None:
        """Full Build with data_home loads SourceManifest for provenance."""
        f = _write_txt(
            tmp_path / "book.txt",
            "You should always validate input carefully before processing.",
        )
        data_home = tmp_path / "data"
        use_case = BuildUseCase(data_home=data_home)
        result = use_case.build_from_sources(
            [str(f)], _spec(), output_dir=tmp_path / "out"
        )
        assert result.skill_dir is not None
        provenance = (
            result.skill_dir / "provenance.yml"
        ).read_text(encoding="utf-8")
        # The AnalyzeUseCase with data_home persists a manifest; the build
        # use case loads it and enriches provenance with the real sha256.
        assert "content_sha256:" in provenance
        # Either the real sha256 (from manifest) or "unknown" if loading
        # failed. With data_home set, the manifest should be available.
        assert "content_sha256: unknown" not in provenance
        # The bundle's source_id should appear in provenance.
        assert result.bundle is not None
        assert result.bundle.source_ids[0] in provenance

    def test_missing_file_returns_errors_no_skill(self, tmp_path: Path) -> None:
        use_case = BuildUseCase()
        result = use_case.build_from_sources(
            [str(tmp_path / "missing.txt")],
            _spec(),
            output_dir=tmp_path / "out",
        )
        assert result.skill_dir is None
        assert result.errors
        assert any(
            err.code == ErrorCode.GATE_FILE_NOT_FOUND for err in result.errors
        )

    def test_multiple_sources_collection_id_derived(self, tmp_path: Path) -> None:
        f1 = _write_txt(
            tmp_path / "a.txt",
            "First principle: always validate input before processing.",
        )
        f2 = _write_txt(
            tmp_path / "b.txt",
            "Second principle: never trust external data without hashing.",
        )
        use_case = BuildUseCase()
        result = use_case.build_from_sources(
            [str(f1), str(f2)], _spec(), output_dir=tmp_path / "out"
        )
        assert result.skill_dir is not None
        assert result.collection_id is not None
        assert result.collection_id.startswith("col-")
        assert result.bundle is not None
        assert len(result.bundle.source_ids) == 2

    def test_invalid_skill_spec_name_raises(self, tmp_path: Path) -> None:
        f = _write_txt(tmp_path / "book.txt", "Content here for the skill.")
        use_case = BuildUseCase()
        with pytest.raises((DomainError, ValueError)):
            # Pydantic ValidationError for invalid name pattern (uppercase)
            # surfaces as ValueError; BuildUseCase may wrap as DomainError.
            use_case.build_from_sources(
                [str(f)],
                SkillSpec(
                    name="Invalid_Name",
                    description="A description long enough to pass validation.",
                    use_when=["x"],
                ),
                output_dir=tmp_path / "out",
            )

    def test_budget_exceeded_raises_no_skill_md(self, tmp_path: Path) -> None:
        """Tight budget triggers BUILD_BUDGET_EXCEEDED with no half-written file."""
        from book2skill.compiler.skill_writer import SkillWriter
        from book2skill.compiler.token_budget import TokenBudget

        f = _write_txt(
            tmp_path / "book.txt",
            "You should always validate input carefully before processing "
            "any data from external sources.",
        )
        # Inject a writer with a tiny budget so the produced SKILL.md exceeds it.
        tight_writer = SkillWriter(
            output_dir=tmp_path / "out",
            budget=TokenBudget(target_min=1, target_max=10, hard_max=10),
        )
        use_case = BuildUseCase(writer=tight_writer)
        with pytest.raises(DomainError) as exc:
            use_case.build_from_sources([str(f)], _spec())
        assert exc.value.code == ErrorCode.BUILD_BUDGET_EXCEEDED
        # No SKILL.md should be left behind on failure.
        assert not (tmp_path / "out" / "SKILL.md").exists()

    def test_explicit_output_dir_overrides_writer(self, tmp_path: Path) -> None:
        """output_dir arg wins over an injected writer's pre-set directory."""
        from book2skill.compiler.skill_writer import SkillWriter

        f = _write_txt(
            tmp_path / "book.txt",
            "You should always validate input carefully before processing.",
        )
        # Writer pre-configured for /wrong-path, but output_dir should override.
        injected = SkillWriter(output_dir=tmp_path / "wrong")
        use_case = BuildUseCase(writer=injected)
        result = use_case.build_from_sources(
            [str(f)], _spec(), output_dir=tmp_path / "explicit-out"
        )
        assert result.skill_dir == (tmp_path / "explicit-out").resolve()
        assert (tmp_path / "explicit-out" / "SKILL.md").exists()
        # The injected writer's dir was NOT used.
        assert not (tmp_path / "wrong" / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# Build from Analysis (build_from_bundle)
# ---------------------------------------------------------------------------


class TestBuildFromBundle:
    """Build from a previously produced AnalysisBundle JSON file."""

    def test_valid_bundle_produces_skill(self, tmp_path: Path) -> None:
        bundle = _bundle()
        bundle_path = tmp_path / "bundle.json"
        bundle_path.write_text(
            bundle.model_dump_json(indent=2), encoding="utf-8"
        )
        use_case = BuildUseCase()
        result = use_case.build_from_bundle(
            bundle_path, _spec(), output_dir=tmp_path / "out"
        )
        assert result.skill_dir is not None
        assert (result.skill_dir / "SKILL.md").exists()
        assert result.collection_id == "col-test"

    def test_missing_bundle_file_raises_input_invalid(
        self, tmp_path: Path
    ) -> None:
        use_case = BuildUseCase()
        with pytest.raises(DomainError) as exc:
            use_case.build_from_bundle(
                tmp_path / "nonexistent.json", _spec(),
                output_dir=tmp_path / "out",
            )
        assert exc.value.code == ErrorCode.BUILD_INPUT_INVALID
        assert "not found" in exc.value.message.lower()

    def test_invalid_json_raises_input_invalid(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        use_case = BuildUseCase()
        with pytest.raises(DomainError) as exc:
            use_case.build_from_bundle(bad, _spec(), output_dir=tmp_path / "out")
        assert exc.value.code == ErrorCode.BUILD_INPUT_INVALID

    def test_empty_candidate_units_raises_input_invalid(
        self, tmp_path: Path
    ) -> None:
        bundle = _bundle(candidate_units=[])
        bundle_path = tmp_path / "bundle.json"
        bundle_path.write_text(
            bundle.model_dump_json(indent=2), encoding="utf-8"
        )
        use_case = BuildUseCase()
        with pytest.raises(DomainError) as exc:
            use_case.build_from_bundle(
                bundle_path, _spec(), output_dir=tmp_path / "out"
            )
        assert exc.value.code == ErrorCode.BUILD_INPUT_INVALID
        assert "no candidate_units" in exc.value.message

    def test_from_bundle_provenance_marks_unknown(self, tmp_path: Path) -> None:
        """Without RawStorage, from-bundle provenance falls back to unknown."""
        bundle = _bundle()
        bundle_path = tmp_path / "bundle.json"
        bundle_path.write_text(
            bundle.model_dump_json(indent=2), encoding="utf-8"
        )
        use_case = BuildUseCase()
        result = use_case.build_from_bundle(
            bundle_path, _spec(), output_dir=tmp_path / "out"
        )
        assert result.skill_dir is not None
        provenance = (
            result.skill_dir / "provenance.yml"
        ).read_text(encoding="utf-8")
        # No manifests available in from-bundle mode → unknown stub or empty.
        assert (
            "content_sha256: unknown" in provenance
            or "no sources recorded" in provenance
            or "sources: []" in provenance
        )

    def test_from_bundle_same_spec_renders_same_skill_md(
        self, tmp_path: Path
    ) -> None:
        """Same SkillSpec → identical SKILL.md regardless of entry point."""
        bundle = _bundle()
        bundle_path = tmp_path / "bundle.json"
        bundle_path.write_text(
            bundle.model_dump_json(indent=2), encoding="utf-8"
        )
        spec = _spec()
        use_case = BuildUseCase()
        r1 = use_case.build_from_bundle(
            bundle_path, spec, output_dir=tmp_path / "from-bundle"
        )
        # Build a second skill from a fresh txt source with the same spec.
        f = _write_txt(
            tmp_path / "book.txt",
            "You should always validate input carefully before processing.",
        )
        r2 = use_case.build_from_sources(
            [str(f)], spec, output_dir=tmp_path / "from-sources"
        )
        # Both produce a SKILL.md; the frontmatter (name/description/use_when)
        # section must match because it is fully derived from the spec.
        from_bundle = (r1.skill_dir / "SKILL.md").read_text(encoding="utf-8")
        from_sources = (r2.skill_dir / "SKILL.md").read_text(encoding="utf-8")
        # Compare the frontmatter block (between --- markers).
        assert from_bundle.split("---")[1] == from_sources.split("---")[1]


# ---------------------------------------------------------------------------
# CLI build command
# ---------------------------------------------------------------------------


class TestBuildCLI:
    """CLI integration tests via Typer's CliRunner."""

    def test_build_full_build_succeeds(self, tmp_path: Path) -> None:
        f = _write_txt(
            tmp_path / "book.txt",
            "You should always validate input carefully before processing.",
        )
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "build",
                str(f),
                "--name",
                "cli-skill",
                "--description",
                "A skill built via the CLI for end-to-end verification.",
                "--use-when",
                "When testing the CLI.",
                "--output-dir",
                str(tmp_path / "cli-out"),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert (tmp_path / "cli-out" / "SKILL.md").exists()

    def test_build_from_analysis_succeeds(self, tmp_path: Path) -> None:
        bundle = _bundle()
        bundle_path = tmp_path / "bundle.json"
        bundle_path.write_text(
            bundle.model_dump_json(indent=2), encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "build",
                "--from-analysis",
                str(bundle_path),
                "--name",
                "cli-from-bundle",
                "--description",
                "A skill built from an AnalysisBundle JSON via the CLI.",
                "--use-when",
                "When resuming from a saved analysis.",
                "--output-dir",
                str(tmp_path / "cli-out"),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert (tmp_path / "cli-out" / "SKILL.md").exists()

    def test_build_without_sources_or_from_analysis_fails(
        self, tmp_path: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "build",
                "--name",
                "x",
                "--description",
                "A description long enough.",
                "--use-when",
                "x",
            ],
        )
        assert result.exit_code != 0

    def test_build_invalid_name_fails(self, tmp_path: Path) -> None:
        f = _write_txt(tmp_path / "book.txt", "Content for testing.")
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "build",
                str(f),
                "--name",
                "INVALID_NAME",
                "--description",
                "A description long enough.",
                "--use-when",
                "x",
                "--output-dir",
                str(tmp_path / "out"),
            ],
        )
        assert result.exit_code != 0

    def test_build_missing_bundle_file_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "build",
                "--from-analysis",
                str(tmp_path / "missing.json"),
                "--name",
                "x",
                "--description",
                "A description long enough.",
                "--use-when",
                "x",
                "--output-dir",
                str(tmp_path / "out"),
            ],
        )
        assert result.exit_code == 1
        assert "BUILD_INPUT_INVALID" in result.stdout


# ---------------------------------------------------------------------------
# BuildResult dataclass
# ---------------------------------------------------------------------------


class TestBuildResult:
    """Direct tests for the BuildResult dataclass defaults."""

    def test_default_result_has_no_skill(self) -> None:
        r = BuildResult()
        assert r.skill_dir is None
        assert r.bundle is None
        assert r.collection_id is None
        assert r.source_manifests == []
        assert r.errors == []

    def test_result_with_manifests_carries_them(self) -> None:
        m = _manifest()
        r = BuildResult(source_manifests=[m])
        assert r.source_manifests == [m]
