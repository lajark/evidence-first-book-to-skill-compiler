"""Tests for the Wiki layer generator (P2)."""

from __future__ import annotations

from book2skill.compiler.wiki_generator import WikiGenerator
from book2skill.domain import KnowledgeRef, KnowledgeStatus, KnowledgeUnit, UnitKind


def _unit(
    *,
    unit_id: str = "u1",
    kind: UnitKind = UnitKind.TECHNIQUE,
    content: str = "Some content here.",
    conditions: list[str] | None = None,
    exceptions: list[str] | None = None,
) -> KnowledgeUnit:
    return KnowledgeUnit(
        unit_id=unit_id,
        kind=kind,
        content=content,
        conditions=conditions or [],
        exceptions=exceptions or [],
        source_refs=[KnowledgeRef(source_id="src1", block_id="b1")],
        confidence=0.8,
        review_status=KnowledgeStatus.APPROVED,
        record_version=1,
    )


class TestWikiGenerator:
    def test_empty_units_produces_no_files(self) -> None:
        result = WikiGenerator([], skill_name="empty").build()
        assert result == {}

    def test_chapters_from_framework_and_principle(self) -> None:
        units = [
            _unit(
                unit_id="f1",
                kind=UnitKind.FRAMEWORK,
                content="The SOLID design framework.\nDetail here.",
            ),
            _unit(
                unit_id="p1",
                kind=UnitKind.PRINCIPLE,
                content="Single Responsibility: one reason to change.",
            ),
        ]
        result = WikiGenerator(units, skill_name="test-skill").build()
        assert "wiki/chapters.md" in result
        chapters = result["wiki/chapters.md"]
        assert "## 1. The SOLID design framework." in chapters
        assert "## 2. Principles" in chapters
        assert "Single Responsibility" in chapters

    def test_glossary_alphabetical(self) -> None:
        units = [
            _unit(
                unit_id="t2",
                kind=UnitKind.TERM,
                content="Zeta: the last letter.",
            ),
            _unit(
                unit_id="t1",
                kind=UnitKind.TERM,
                content="Alpha: the first letter.",
            ),
        ]
        result = WikiGenerator(units, skill_name="glossary").build()
        assert "wiki/glossary.md" in result
        glossary = result["wiki/glossary.md"]
        # Alpha appears before Zeta (alphabetical by first line).
        assert glossary.index("**Alpha") < glossary.index("**Zeta")

    def test_patterns_and_anti_patterns(self) -> None:
        units = [
            _unit(
                unit_id="p1",
                kind=UnitKind.PRINCIPLE,
                content="Favour composition over inheritance.",
            ),
            _unit(
                unit_id="a1",
                kind=UnitKind.ANTI_PATTERN,
                content="God object anti-pattern.",
            ),
        ]
        result = WikiGenerator(units, skill_name="patterns").build()
        assert "wiki/patterns.md" in result
        patterns = result["wiki/patterns.md"]
        assert "## Patterns" in patterns
        assert "## Anti-Patterns" in patterns
        assert "Favour composition" in patterns
        assert "God object" in patterns

    def test_cheatsheet_from_techniques(self) -> None:
        units = [
            _unit(
                unit_id="tech1",
                kind=UnitKind.TECHNIQUE,
                content=(
                    "Extract interface.\n"
                    "Then implement.\n"
                    "Then test.\n"
                    "Then deploy.\n"
                    "Then monitor."
                ),
            ),
            _unit(
                unit_id="cl1",
                kind=UnitKind.CHECKLIST,
                content="Check naming conventions.\nCheck types.",
            ),
        ]
        result = WikiGenerator(units, skill_name="cheat").build()
        assert "wiki/cheatsheet.md" in result
        cheat = result["wiki/cheatsheet.md"]
        assert "Technique: Extract interface." in cheat
        assert "Checklist: Check naming conventions." in cheat
        # Condensed: only first 3 lines of content.
        assert "> Extract interface." in cheat
        assert "> Then implement." in cheat
        assert "> Then test." in cheat
        # Lines 4 and 5 are truncated.
        assert "> Then deploy." not in cheat
        assert "> Then monitor." not in cheat

    def test_conditions_and_exceptions_rendered(self) -> None:
        units = [
            _unit(
                unit_id="p1",
                kind=UnitKind.FRAMEWORK,
                content="My framework.",
                conditions=["When A is true"],
                exceptions=["Unless B occurs"],
            ),
        ]
        result = WikiGenerator(units, skill_name="cond").build()
        chapters = result["wiki/chapters.md"]
        assert "**Conditions:**" in chapters
        assert "- When A is true" in chapters
        assert "**Exceptions:**" in chapters
        assert "- Unless B occurs" in chapters

    def test_only_technique_units_skips_chapters(self) -> None:
        """When no framework/principle, no chapters file is produced."""
        units = [
            _unit(
                unit_id="t1",
                kind=UnitKind.TECHNIQUE,
                content="A technique.",
            ),
        ]
        result = WikiGenerator(units, skill_name="tech-only").build()
        assert "wiki/chapters.md" not in result
        assert "wiki/cheatsheet.md" in result

    def test_skill_name_in_headings(self) -> None:
        units = [
            _unit(
                unit_id="f1",
                kind=UnitKind.FRAMEWORK,
                content="A framework.",
            ),
        ]
        result = WikiGenerator(units, skill_name="my-cool-skill").build()
        assert "my-cool-skill" in result["wiki/chapters.md"]
