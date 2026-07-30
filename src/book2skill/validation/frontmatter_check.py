"""Frontmatter check: validates the YAML frontmatter of ``SKILL.md``.

PRD FR-07 requires the frontmatter to be present and well-formed. This check
mirrors the rules enforced by :class:`~book2skill.compiler.SkillIR`:

- The file must start with a ``---``-delimited YAML block.
- ``name`` is required and must match the Skill slug pattern
  ``^[a-z0-9]+(?:-[a-z0-9]+)*$`` (same pattern as
  :data:`~book2skill.compiler.ir_builder._SKILL_NAME_PATTERN`).
- ``description`` is required and at least 10 characters long.

The check uses ``yaml.safe_load`` (``pyyaml`` is already a runtime dependency)
so no new dependency is introduced.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from book2skill.validation.models import BaseCheck, CheckStatus, Finding

#: Skill name pattern, mirroring ``schemas/skill-ir.schema.json`` /
#: :data:`~book2skill.compiler.ir_builder._SKILL_NAME_PATTERN`.
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Minimum description length, mirroring :class:`~book2skill.compiler.SkillIR`.
_MIN_DESCRIPTION_LEN = 10

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<yaml>.*?)\n---\s*(?:\n|$)", re.DOTALL
)


class FrontmatterCheck(BaseCheck):
    """Validate the YAML frontmatter of ``SKILL.md``."""

    check_id = "frontmatter"

    def _run(self, skill_dir: Path) -> list[Finding]:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            # Structural absence is handled by the Validator; here we just
            # report a soft finding so the check is not silently skipped.
            return [
                Finding(
                    severity=CheckStatus.FAIL,
                    code="frontmatter.missing_skill_md",
                    location="SKILL.md",
                    message="SKILL.md not found; cannot check frontmatter.",
                )
            ]

        text = skill_md.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(text)
        if match is None:
            return [
                Finding(
                    severity=CheckStatus.FAIL,
                    code="frontmatter.missing",
                    location="SKILL.md:1",
                    message="SKILL.md has no YAML frontmatter block.",
                )
            ]

        try:
            data = yaml.safe_load(match.group("yaml")) or {}
        except yaml.YAMLError as exc:
            return [
                Finding(
                    severity=CheckStatus.FAIL,
                    code="frontmatter.malformed_yaml",
                    location="SKILL.md:1",
                    message=f"Frontmatter YAML is malformed: {exc}",
                )
            ]

        findings: list[Finding] = []
        if not isinstance(data, dict):
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="frontmatter.not_mapping",
                    location="SKILL.md:1",
                    message="Frontmatter must be a YAML mapping at the top level.",
                )
            )
            return findings

        name = data.get("name")
        if not name or not isinstance(name, str):
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="frontmatter.name_missing",
                    location="SKILL.md:2",
                    message="Frontmatter is missing the required 'name' field.",
                )
            )
        elif not _SKILL_NAME_PATTERN.match(name):
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="frontmatter.name_pattern",
                    location="SKILL.md:2",
                    message=(
                        f"Frontmatter 'name'='{name}' does not match the "
                        "slug pattern ^[a-z0-9]+(?:-[a-z0-9]+)*$."
                    ),
                )
            )

        description = data.get("description")
        if not description or not isinstance(description, str):
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="frontmatter.description_missing",
                    location="SKILL.md:3",
                    message="Frontmatter is missing the required 'description' field.",
                )
            )
        elif len(description) < _MIN_DESCRIPTION_LEN:
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="frontmatter.description_short",
                    location="SKILL.md:3",
                    message=(
                        f"Frontmatter 'description' is {len(description)} chars; "
                        f"minimum is {_MIN_DESCRIPTION_LEN}."
                    ),
                )
            )

        return findings


__all__ = ["FrontmatterCheck"]
