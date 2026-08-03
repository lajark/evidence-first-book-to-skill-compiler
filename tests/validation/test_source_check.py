"""Tests for :mod:`book2skill.validation.source_check`."""

from __future__ import annotations

from pathlib import Path

from book2skill.validation.models import CheckStatus
from book2skill.validation.source_check import SourceCheck

_VALID_FRONTMATTER = (
    "---\n"
    "name: my-skill\n"
    "description: A valid skill that does something useful.\n"
    "---\n"
)


def _make_skill(
    skill_dir: Path,
    *,
    skill_md: str | None = None,
    provenance: str | None = None,
    references: dict[str, str] | None = None,
) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        skill_md or _VALID_FRONTMATTER + "# Skill\n", encoding="utf-8"
    )
    if provenance is not None:
        (skill_dir / "provenance.yml").write_text(provenance, encoding="utf-8")
    if references:
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        for rel, content in references.items():
            (refs_dir / rel).write_text(content, encoding="utf-8")
    return skill_dir


class TestSourceCheck:
    def test_full_coverage_passes(self, tmp_path: Path) -> None:
        provenance = (
            "schema_version: 1\n"
            "skill_name: my-skill\n"
            "sources:\n"
            "  - source_id: src-1\n"
            "    content_sha256: abc\n"
        )
        references = {
            "techniques.md": (
                "# Techniques\n\n## u1\n\nSome text.\n\n**Sources:**\n"
                "- src-1 / block-a\n"
            )
        }
        _make_skill(tmp_path, provenance=provenance, references=references)
        result = SourceCheck().run(tmp_path)
        assert result.status == CheckStatus.PASS
        assert result.evidence == []

    def test_undeclared_source_fails(self, tmp_path: Path) -> None:
        provenance = (
            "sources:\n  - source_id: src-1\n    content_sha256: abc\n"
        )
        references = {
            "techniques.md": (
                "**Sources:**\n- src-1 / block-a\n- src-2 / block-b\n"
            )
        }
        _make_skill(tmp_path, provenance=provenance, references=references)
        result = SourceCheck().run(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert "source.undeclared" in result.evidence

    def test_orphan_declared_source_warns(self, tmp_path: Path) -> None:
        provenance = (
            "sources:\n"
            "  - source_id: src-1\n    content_sha256: abc\n"
            "  - source_id: src-2\n    content_sha256: def\n"
        )
        references = {
            "techniques.md": "**Sources:**\n- src-1 / block-a\n"
        }
        _make_skill(tmp_path, provenance=provenance, references=references)
        result = SourceCheck().run(tmp_path)
        assert result.status == CheckStatus.WARN
        assert "source.orphan" in result.evidence

    def test_duplicate_source_id_fails(self, tmp_path: Path) -> None:
        provenance = (
            "sources:\n"
            "  - source_id: src-1\n    content_sha256: abc\n"
            "  - source_id: src-1\n    content_sha256: def\n"
        )
        references = {"techniques.md": "**Sources:**\n- src-1 / block-a\n"}
        _make_skill(tmp_path, provenance=provenance, references=references)
        result = SourceCheck().run(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert "source.duplicate_id" in result.evidence

    def test_missing_provenance_warns_for_empty_skill(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, provenance=None)
        result = SourceCheck().run(tmp_path)
        assert result.status == CheckStatus.WARN
        assert "source.no_provenance" in result.evidence

    def test_missing_provenance_with_knowledge_fails(self, tmp_path: Path) -> None:
        skill_md = _VALID_FRONTMATTER + "# Skill\n\nApply this decision rule.\n"
        _make_skill(tmp_path, skill_md=skill_md, provenance=None)

        result = SourceCheck().run(tmp_path)

        assert result.status == CheckStatus.FAIL
        assert "source.no_provenance" in result.evidence

    def test_empty_provenance_sources_passes(self, tmp_path: Path) -> None:
        # Empty sources list with no citations is a clean pass.
        provenance = "sources: []\n"
        _make_skill(tmp_path, provenance=provenance)
        result = SourceCheck().run(tmp_path)
        assert result.status == CheckStatus.PASS

    def test_empty_provenance_with_reference_content_fails(
        self, tmp_path: Path
    ) -> None:
        _make_skill(
            tmp_path,
            provenance="sources: []\n",
            references={
                "techniques.md": "# Techniques\n\nApply this technique carefully.\n"
            },
        )

        result = SourceCheck().run(tmp_path)

        assert result.status == CheckStatus.FAIL
        assert "source.empty_provenance" in result.evidence

    def test_empty_provenance_with_citation_reports_undeclared_source(
        self, tmp_path: Path
    ) -> None:
        _make_skill(
            tmp_path,
            provenance="sources: []\n",
            references={
                "techniques.md": (
                    "# Techniques\n\nApply this technique.\n\n"
                    "**Sources:**\n- src-1 / block-a\n"
                )
            },
        )

        result = SourceCheck().run(tmp_path)

        assert result.status == CheckStatus.FAIL
        assert "source.empty_provenance" in result.evidence
        assert "source.undeclared" in result.evidence

    def test_missing_reference_link_warns(self, tmp_path: Path) -> None:
        skill_md = (
            _VALID_FRONTMATTER
            + "# Skill\n\nDetailed references: `references/missing.md`\n"
        )
        provenance = "sources:\n  - source_id: src-1\n    content_sha256: abc\n"
        _make_skill(
            tmp_path,
            skill_md=skill_md,
            provenance=provenance,
            references={"techniques.md": "**Sources:**\n- src-1 / block-a\n"},
        )
        result = SourceCheck().run(tmp_path)
        assert "links.missing_reference" in result.evidence
