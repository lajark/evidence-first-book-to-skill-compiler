"""Wiki layer generator for compiled Skills (P2, PRD FR-04 backlog).

Produces derived view files under ``wiki/`` from the same
:class:`~book2skill.domain.KnowledgeUnit` records that feed the SkillIR.
These are human-readable navigation aids — they never duplicate the
canonical content in ``SKILL.md`` or ``references/``; they reorganise it
for different reading modes.

Generated files (only when the source units exist):

- ``wiki/chapters.md`` — chapter-structured overview built from
  framework/principle units (the methodology skeleton).
- ``wiki/glossary.md`` — alphabetical glossary of ``term`` units.
- ``wiki/patterns.md`` — patterns & anti-patterns summary.
- ``wiki/cheatsheet.md`` — condensed quick-reference from
  technique/checklist/decision_rule units.

The generator is pure (no I/O); :class:`~book2skill.compiler.SkillWriter`
writes the returned ``{rel_path: content}`` mapping to disk.
"""

from __future__ import annotations

from book2skill.domain import KnowledgeUnit, UnitKind

__all__ = ["WikiGenerator"]


class WikiGenerator:
    """Build ``wiki/*.md`` derived views from knowledge units.

    Args:
        units: Usable (non-rejected/superseded) knowledge units.
        skill_name: The Skill slug, used in headings.
    """

    def __init__(
        self, units: list[KnowledgeUnit], *, skill_name: str
    ) -> None:
        self._units = units
        self._name = skill_name

    def build(self) -> dict[str, str]:
        """Return a mapping of ``wiki/<file>.md`` → Markdown content.

        Only kinds with at least one unit produce a file.
        """
        result: dict[str, str] = {}

        chapters = self._render_chapters()
        if chapters:
            result["wiki/chapters.md"] = chapters

        glossary = self._render_glossary()
        if glossary:
            result["wiki/glossary.md"] = glossary

        patterns = self._render_patterns()
        if patterns:
            result["wiki/patterns.md"] = patterns

        cheatsheet = self._render_cheatsheet()
        if cheatsheet:
            result["wiki/cheatsheet.md"] = cheatsheet

        return result

    # -- renderers --------------------------------------------------------

    def _render_chapters(self) -> str:
        """Chapter-structured overview from framework/principle units."""
        framework = [
            u for u in self._units if _kind(u) == UnitKind.FRAMEWORK
        ]
        principles = [
            u for u in self._units if _kind(u) == UnitKind.PRINCIPLE
        ]
        if not framework and not principles:
            return ""

        lines: list[str] = [f"# Chapters — {self._name}", ""]
        chapter_num = 0

        for u in framework:
            chapter_num += 1
            heading = _first_line(u.content) or f"Framework {chapter_num}"
            lines.append(f"## {chapter_num}. {heading}")
            lines.append("")
            lines.append(u.content)
            _append_conditions(u, lines)
            lines.append("")

        if principles:
            chapter_num += 1
            lines.append(f"## {chapter_num}. Principles")
            lines.append("")
            for u in principles:
                lines.append(f"### {_first_line(u.content) or u.unit_id}")
                lines.append("")
                lines.append(u.content)
                _append_conditions(u, lines)
                lines.append("")

        return "\n".join(lines) + "\n"

    def _render_glossary(self) -> str:
        """Alphabetical glossary of term units."""
        terms = [u for u in self._units if _kind(u) == UnitKind.TERM]
        if not terms:
            return ""

        lines: list[str] = ["# Glossary", ""]
        for u in sorted(terms, key=lambda x: _first_line(x.content).lower()):
            term = _first_line(u.content) or u.unit_id
            lines.append(f"**{term}**")
            lines.append("")
            lines.append(u.content)
            lines.append("")
        return "\n".join(lines) + "\n"

    def _render_patterns(self) -> str:
        """Patterns & anti-patterns summary."""
        patterns = [
            u for u in self._units if _kind(u) == UnitKind.PRINCIPLE
        ]
        anti = [
            u for u in self._units if _kind(u) == UnitKind.ANTI_PATTERN
        ]
        if not patterns and not anti:
            return ""

        lines: list[str] = ["# Patterns & Anti-Patterns", ""]
        if patterns:
            lines.append("## Patterns")
            lines.append("")
            for u in patterns:
                lines.append(f"- {_first_line(u.content) or u.unit_id}")
            lines.append("")
        if anti:
            lines.append("## Anti-Patterns")
            lines.append("")
            for u in anti:
                lines.append(f"- {_first_line(u.content) or u.unit_id}")
            lines.append("")
        return "\n".join(lines) + "\n"

    def _render_cheatsheet(self) -> str:
        """Condensed quick-reference from technique/checklist/decision_rule."""
        cheat_kinds = {
            UnitKind.TECHNIQUE,
            UnitKind.CHECKLIST,
            UnitKind.DECISION_RULE,
        }
        units = [u for u in self._units if _kind(u) in cheat_kinds]
        if not units:
            return ""

        lines: list[str] = [f"# Cheatsheet — {self._name}", ""]
        for u in units:
            label = _kind(u).value.replace("_", " ").title()
            lines.append(f"## {label}: {_first_line(u.content) or u.unit_id}")
            lines.append("")
            # Condense: first 3 lines of content.
            content_lines = u.content.strip().splitlines()[:3]
            for cl in content_lines:
                lines.append(f"> {cl}")
            lines.append("")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kind(u: KnowledgeUnit) -> UnitKind:
    """Return the UnitKind, handling str-serialised enums."""
    return UnitKind(u.kind) if isinstance(u.kind, str) else u.kind


def _first_line(text: str) -> str:
    """Return the stripped first line of *text*, or empty string."""
    if not text:
        return ""
    return text.strip().splitlines()[0].strip()


def _append_conditions(u: KnowledgeUnit, lines: list[str]) -> None:
    """Append conditions/exceptions to *lines* if present."""
    if u.conditions:
        lines.append("")
        lines.append("**Conditions:**")
        for c in u.conditions:
            lines.append(f"- {c}")
    if u.exceptions:
        lines.append("")
        lines.append("**Exceptions:**")
        for e in u.exceptions:
            lines.append(f"- {e}")
