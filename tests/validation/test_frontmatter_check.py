"""Tests for :mod:`book2skill.validation.frontmatter_check`."""

from __future__ import annotations

from pathlib import Path

from book2skill.validation.frontmatter_check import FrontmatterCheck
from book2skill.validation.models import CheckStatus

_VALID_FRONTMATTER = (
    "---\n"
    "name: my-skill\n"
    "description: A valid skill that does something useful.\n"
    "---\n"
    "\n"
    "# My Skill\n"
)


def _write_skill_md(skill_dir: Path, content: str) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")
    return skill_md


class TestFrontmatterCheck:
    def test_valid_frontmatter_passes(self, tmp_path: Path) -> None:
        _write_skill_md(tmp_path, _VALID_FRONTMATTER)
        result = FrontmatterCheck().run(tmp_path)
        assert result.status == CheckStatus.PASS
        assert result.evidence == []

    def test_missing_name_field_fails(self, tmp_path: Path) -> None:
        content = (
            "---\n"
            "description: A skill with no name field.\n"
            "---\n"
            "# No name\n"
        )
        _write_skill_md(tmp_path, content)
        result = FrontmatterCheck().run(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert "frontmatter.name_missing" in result.evidence

    def test_name_pattern_violation_fails(self, tmp_path: Path) -> None:
        content = (
            "---\n"
            "name: Bad_Name_123\n"
            "description: A skill with an invalid slug name.\n"
            "---\n"
            "# Bad name\n"
        )
        _write_skill_md(tmp_path, content)
        result = FrontmatterCheck().run(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert "frontmatter.name_pattern" in result.evidence

    def test_short_description_fails(self, tmp_path: Path) -> None:
        content = "---\nname: my-skill\ndescription: short\n---\n# x\n"
        _write_skill_md(tmp_path, content)
        result = FrontmatterCheck().run(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert "frontmatter.description_short" in result.evidence

    def test_no_frontmatter_fails(self, tmp_path: Path) -> None:
        _write_skill_md(tmp_path, "# No frontmatter here\n\nJust prose.\n")
        result = FrontmatterCheck().run(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert "frontmatter.missing" in result.evidence

    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        content = "---\nname: my-skill\n: : broken\ndescription: ok one.\n---\n# x\n"
        _write_skill_md(tmp_path, content)
        result = FrontmatterCheck().run(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert "frontmatter.malformed_yaml" in result.evidence
