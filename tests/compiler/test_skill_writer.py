"""Tests for the SkillWriter directory generator."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from book2skill.compiler.ir_builder import SkillIR, SkillUsage, WorkflowStep
from book2skill.compiler.skill_writer import SkillWriter
from book2skill.compiler.token_budget import TokenBudget
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.domain.models import Confidentiality, SourceFormat, SourceManifest


def _ir(
    *,
    name: str = "test-skill",
    description: str = "A test skill that compiles knowledge into a usable form.",
    references: list[str] | None = None,
) -> SkillIR:
    return SkillIR(
        name=name,
        description=description,
        usage=SkillUsage(
            use_when=["You need structured knowledge."],
            do_not_use_when=["The source is not legally held."],
        ),
        workflow=[
            WorkflowStep(step="Define goals", description="Clarify objectives first."),
            WorkflowStep(step="Apply technique", refs=["ku-1"]),
        ],
        knowledge_refs=["ku-1"],
        references=references or [],
    )


@pytest.fixture()
def writer(tmp_path: Path) -> SkillWriter:
    return SkillWriter(output_dir=tmp_path / "skill-out")


class TestWriteDirectory:
    def test_creates_all_required_artifacts(
        self, writer: SkillWriter
    ) -> None:
        out = writer.write(
            _ir(), references={"references/techniques.md": "# Techniques\n"}
        )
        assert out.exists()
        assert (out / "SKILL.md").exists()
        assert (out / "references" / "techniques.md").exists()
        assert (out / "assets").is_dir()
        assert (out / "provenance.yml").exists()
        assert (out / "quality-report.md").exists()

    def test_skill_md_contains_frontmatter(
        self, writer: SkillWriter
    ) -> None:
        writer.write(_ir())
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "name: test-skill" in content
        assert "description:" in content

    def test_description_with_colon_keeps_frontmatter_valid(
        self, writer: SkillWriter
    ) -> None:
        description = "Route: use the indexed references before drafting output."
        writer.write(_ir(description=description))
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = content.split("---\n", 2)[1]
        parsed = yaml.safe_load(frontmatter)
        assert parsed["description"] == description

    def test_skill_md_contains_sections(
        self, writer: SkillWriter
    ) -> None:
        writer.write(_ir())
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "## Use when" in content
        assert "## Do not use when" in content
        assert "## Required inputs" in content
        assert "## Workflow" in content
        assert "## Conditions" in content
        assert "## Exceptions and escalation" in content
        assert "## Output contract" in content
        assert "<!-- runtime-scaffolding -->" not in content
        assert "## Runtime execution scaffolding" not in content
        assert "## Examples" in content
        assert "## Evidence and limitations" in content

    def test_runtime_scaffolding_is_rendered_for_time_boxed_methods(
        self, writer: SkillWriter
    ) -> None:
        ir = _ir(
            name="pomodoro-skill",
            description="Run a Pomodoro session and record estimate deviation.",
        )
        writer.write(ir)
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "## Runtime execution scaffolding" in content
        assert "Available time（可用时间）" in content
        assert "Deviation/cause（偏差/原因）" in content

    def test_runtime_scaffolding_does_not_contaminate_other_domains(
        self, writer: SkillWriter
    ) -> None:
        ir = _ir(
            name="sunzi-strategy",
            description="Compare objectives, intelligence, constraints, and options.",
        )
        writer.write(ir)
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "## Runtime execution scaffolding" not in content
        assert "Estimate feedback record" not in content

    def test_habit_scaffolding_uses_habit_artifacts(
        self, writer: SkillWriter
    ) -> None:
        ir = _ir(
            name="micro-habits",
            description="Build a micro-habit with a cue, reward, and daily check.",
        )
        writer.write(ir)
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "## Habit execution scaffolding" in content
        assert "Micro-habit plan" in content
        assert "Daily check record" in content
        assert "Estimate feedback record" not in content

    def test_skill_md_renders_domain_contract_and_case_example(
        self, writer: SkillWriter
    ) -> None:
        ir = _ir().model_copy(
            update={
                "required_inputs": ["A signed requirements brief."],
                "outputs": ["A reviewed delivery plan."],
                "conditions": ["Use only with confirmed scope."],
                "exceptions": ["Escalate conflicting requirements."],
                "examples": [
                    {
                        "unit_id": "case-1",
                        "scenario": "A scoped request produced a delivery plan.",
                    }
                ],
            }
        )

        writer.write(ir)
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")

        assert "- A signed requirements brief." in content
        assert "- A reviewed delivery plan." in content
        assert "- Use only with confirmed scope." in content
        assert "- Escalate conflicting requirements." in content
        assert "- A scoped request produced a delivery plan. (case: case-1)" in content

    def test_use_when_populated(self, writer: SkillWriter) -> None:
        writer.write(_ir())
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "- You need structured knowledge." in content

    def test_do_not_use_when_populated(self, writer: SkillWriter) -> None:
        writer.write(_ir())
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "- The source is not legally held." in content

    def test_empty_do_not_use_when_shows_not_applicable(
        self, writer: SkillWriter
    ) -> None:
        ir = SkillIR(
            name="x-skill",
            description="A valid description here for testing.",
            usage=SkillUsage(use_when=["x"], do_not_use_when=[]),
            workflow=[WorkflowStep(step="Step")],
        )
        writer.write(ir)
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "- Not applicable." in content

    def test_workflow_steps_rendered_numbered(
        self, writer: SkillWriter
    ) -> None:
        writer.write(_ir())
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "1. Define goals" in content
        assert "2. Apply technique" in content
        # description on next line
        assert "Clarify objectives first." in content

    def test_workflow_does_not_repeat_compiler_generated_preview(
        self, writer: SkillWriter
    ) -> None:
        ir = _ir().model_copy(
            update={
                "workflow": [
                    WorkflowStep(
                        step=(
                            "Principle: Keep the required action deliberately "
                            "small..."
                        ),
                        description=(
                            "Keep the required action deliberately small so it remains "
                            "reliable on low-motivation days."
                        ),
                    )
                ]
            }
        )

        writer.write(ir)
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")

        assert content.count("Keep the required action deliberately small") == 1

    def test_references_routing_appended(
        self, writer: SkillWriter
    ) -> None:
        ir = _ir(references=["references/techniques.md"])
        writer.write(ir)
        content = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "`references/techniques.md`" in content

    def test_reference_files_written(self, writer: SkillWriter) -> None:
        refs = {
            "references/techniques.md": "# Techniques\n\nUse TDD.\n",
            "references/terms.md": "# Terms\n\nTDD: test-driven dev.\n",
        }
        writer.write(_ir(), references=refs)
        assert (writer._output_dir / "references" / "techniques.md").read_text(
            encoding="utf-8"
        ).startswith("# Techniques")
        assert (writer._output_dir / "references" / "terms.md").exists()


class TestProvenanceAndQualityReport:
    def test_provenance_contains_skill_name(
        self, writer: SkillWriter
    ) -> None:
        writer.write(_ir())
        content = (writer._output_dir / "provenance.yml").read_text(
            encoding="utf-8"
        )
        assert "skill_name: test-skill" in content
        assert "schema_version: 1" in content
        assert "review_status: candidate" in content
        assert "built_at:" in content

    def test_provenance_with_source_ids_marks_unknown(
        self, writer: SkillWriter
    ) -> None:
        writer.write(_ir(), source_ids=["src-1", "src-2"])
        content = (writer._output_dir / "provenance.yml").read_text(
            encoding="utf-8"
        )
        assert "source_id: src-1" in content
        assert "source_id: src-2" in content
        assert "title: unknown" in content
        assert "content_sha256: unknown" in content

    def test_provenance_without_source_ids_has_placeholder(
        self, writer: SkillWriter
    ) -> None:
        writer.write(_ir())
        content = (writer._output_dir / "provenance.yml").read_text(
            encoding="utf-8"
        )
        # Empty sources emit a placeholder/empty list (no real metadata).
        assert "no sources recorded" in content or "sources: []" in content

    def test_quality_report_is_stub(self, writer: SkillWriter) -> None:
        writer.write(_ir())
        content = (writer._output_dir / "quality-report.md").read_text(
            encoding="utf-8"
        )
        assert "# Quality Report" in content
        assert "test-skill" in content


class TestBudgetEnforcement:
    def test_within_budget_succeeds(self, tmp_path: Path) -> None:
        writer = SkillWriter(
            output_dir=tmp_path / "ok",
            budget=TokenBudget(target_min=1, target_max=5000),
        )
        writer.write(_ir())
        assert (tmp_path / "ok" / "SKILL.md").exists()

    def test_exceeds_hard_max_raises(self, tmp_path: Path) -> None:
        writer = SkillWriter(
            output_dir=tmp_path / "over",
            budget=TokenBudget(target_min=1, target_max=10, hard_max=10),
        )
        with pytest.raises(DomainError) as exc:
            writer.write(_ir())
        assert exc.value.code == ErrorCode.BUILD_BUDGET_EXCEEDED
        # No SKILL.md should be left behind on failure.
        assert not (tmp_path / "over" / "SKILL.md").exists()


class TestSafety:
    def test_reference_path_traversal_blocked(
        self, writer: SkillWriter
    ) -> None:
        with pytest.raises(DomainError) as exc:
            writer.write(
                _ir(),
                references={"../../../escape.md": "bad"},
            )
        assert exc.value.code == ErrorCode.BUILD_IR_FAILED

    def test_idempotent_overwrite(self, writer: SkillWriter) -> None:
        writer.write(_ir())
        first = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        # Second write should overwrite cleanly, not duplicate.
        writer.write(_ir())
        second = (writer._output_dir / "SKILL.md").read_text(encoding="utf-8")
        assert first == second


# ---------------------------------------------------------------------------
# Source-manifest provenance enrichment (TASK-013)
# ---------------------------------------------------------------------------


def _manifest(
    *,
    source_id: str = "abc123def456",
    sha256: str = "a" * 64,
    original_name: str | None = "book.pdf",
    fmt: SourceFormat = SourceFormat.PDF,
) -> SourceManifest:
    return SourceManifest(
        source_id=source_id,
        version=1,
        original_name=original_name,
        content_sha256=sha256,
        format=fmt,
        rights_confirmed=True,
        ingested_at=datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc),
        extractor="PdfExtractor",
        extractor_version="1.0",
        confidentiality=Confidentiality.PERSONAL,
    )


class TestProvenanceWithManifests:
    """provenance.yml enrichment from real SourceManifest records."""

    def test_manifest_populates_real_sha256_and_title(self, tmp_path: Path) -> None:
        writer = SkillWriter(output_dir=tmp_path / "out")
        m = _manifest(original_name="my-book.pdf")
        writer.write(_ir(), source_manifests=[m])
        content = (tmp_path / "out" / "provenance.yml").read_text(encoding="utf-8")
        assert f"source_id: {m.source_id}" in content
        assert f"content_sha256: {'a' * 64}" in content
        assert "title: my-book.pdf" in content
        assert "format: pdf" in content
        assert "ingested_at:" in content
        # author/edition remain unknown — SourceManifest has no such fields.
        assert "author: unknown" in content
        assert "edition: unknown" in content

    def test_manifest_without_original_name_falls_back_to_source_id(
        self, tmp_path: Path
    ) -> None:
        writer = SkillWriter(output_dir=tmp_path / "out")
        m = _manifest(original_name=None)
        writer.write(_ir(), source_manifests=[m])
        content = (tmp_path / "out" / "provenance.yml").read_text(encoding="utf-8")
        # title falls back to source_id
        assert f"title: {m.source_id}" in content

    def test_multiple_manifests_each_get_entry(self, tmp_path: Path) -> None:
        writer = SkillWriter(output_dir=tmp_path / "out")
        m1 = _manifest(
            source_id="src111111111", sha256="b" * 64, original_name="a.pdf"
        )
        m2 = _manifest(
            source_id="src222222222",
            sha256="c" * 64,
            original_name="b.epub",
            fmt=SourceFormat.EPUB,
        )
        writer.write(_ir(), source_manifests=[m1, m2])
        content = (tmp_path / "out" / "provenance.yml").read_text(encoding="utf-8")
        assert "source_id: src111111111" in content
        assert "source_id: src222222222" in content
        assert "content_sha256: " + "b" * 64 in content
        assert "content_sha256: " + "c" * 64 in content
        assert "format: epub" in content

    def test_manifests_are_written_in_source_id_order(self, tmp_path: Path) -> None:
        writer = SkillWriter(output_dir=tmp_path / "out")
        source_b = _manifest(source_id="source-b", original_name="b.pdf")
        source_a = _manifest(source_id="source-a", original_name="a.pdf")

        writer.write(_ir(), source_manifests=[source_b, source_a])

        content = (tmp_path / "out" / "provenance.yml").read_text(encoding="utf-8")
        assert content.index("source-a") < content.index("source-b")

    def test_manifests_take_precedence_over_source_ids(
        self, tmp_path: Path
    ) -> None:
        """When both are passed, manifests win (real data beats stub)."""
        writer = SkillWriter(output_dir=tmp_path / "out")
        m = _manifest()
        writer.write(_ir(), source_ids=["legacy-id"], source_manifests=[m])
        content = (tmp_path / "out" / "provenance.yml").read_text(encoding="utf-8")
        assert m.source_id in content
        assert "legacy-id" not in content
        # No "unknown" stub for sha256 since manifest provided real value.
        assert "content_sha256: unknown" not in content

    def test_source_ids_only_still_uses_legacy_stub(self, tmp_path: Path) -> None:
        """Backward compatibility: source_ids without manifests -> unknown."""
        writer = SkillWriter(output_dir=tmp_path / "out")
        writer.write(_ir(), source_ids=["src-1"])
        content = (tmp_path / "out" / "provenance.yml").read_text(encoding="utf-8")
        assert "source_id: src-1" in content
        assert "content_sha256: unknown" in content
        assert "title: unknown" in content

    def test_neither_manifests_nor_source_ids_emits_placeholder(
        self, tmp_path: Path
    ) -> None:
        writer = SkillWriter(output_dir=tmp_path / "out")
        writer.write(_ir())
        content = (tmp_path / "out" / "provenance.yml").read_text(encoding="utf-8")
        assert "no sources recorded" in content or "[]" in content

    def test_title_with_colon_is_yaml_escaped(self, tmp_path: Path) -> None:
        writer = SkillWriter(output_dir=tmp_path / "out")
        m = _manifest(original_name="book: a subtitle.pdf")
        writer.write(_ir(), source_manifests=[m])
        content = (tmp_path / "out" / "provenance.yml").read_text(encoding="utf-8")
        # The colon-containing title must be quoted to keep YAML valid
        # (PyYAML may use single or double quotes).
        assert "title: 'book: a subtitle.pdf'" in content or (
            'title: "book: a subtitle.pdf"' in content
        )
