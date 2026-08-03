"""Source coverage, orphan reference and duplicate-id check (PRD FR-07).

The check parses ``provenance.yml`` to enumerate declared ``source_id`` values,
then scans ``SKILL.md`` plus every ``references/*.md`` for source citations
in the format emitted by
:func:`~book2skill.compiler.ir_builder.IRBuilder._render_reference`:

    **Sources:**
    - <source_id> / <block_id>
    - <source_id> / <block_id> — "optional quote"

Findings:

- Duplicate ``source_id`` in provenance.yml → ``fail``.
- A cited ``source_id`` that is not declared in provenance.yml → ``fail``
  (``source.undeclared``).
- A declared ``source_id`` that is never cited anywhere → ``warn``
  (``source.orphan``).
- Missing or empty provenance for non-empty knowledge/reference content →
  ``fail``. A missing ledger for an otherwise empty Skill skeleton remains a
  ``warn`` because there is no source-bearing content to cover.
- A ``references/<file>.md`` referenced from ``SKILL.md`` (e.g. in a routing
  line) that does not exist on disk → ``warn`` (``links.missing_reference``).

``provenance.yml`` parsing uses ``yaml.safe_load`` (already a runtime
dependency); the file is hand-written by :class:`~book2skill.compiler.SkillWriter`
so it is plain YAML 1.1.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from book2skill.validation.models import BaseCheck, CheckStatus, Finding

#: A source citation line: ``- <source_id> / <block_id>`` optionally followed
#: by `` — "quote"``. Anchored on the leading ``- `` so incidental slashes in
#: prose do not trigger false matches.
_SOURCE_CITATION_RE = re.compile(
    r"^\s*-\s+(?P<source_id>[^\s/]+)\s*/\s*(?P<block_id>[^\s]+)"
    r"(?:\s+—\s+\".*\")?\s*$",
    re.MULTILINE,
)

#: Markdown link reference inside the routing line of SKILL.md, e.g.
#: ``Detailed references: `references/techniques.md`, `references/terms.md```.
_REFERENCE_FILE_RE = re.compile(r"`(references/[A-Za-z0-9_\-/]+\.md)`")

#: YAML frontmatter at the start of SKILL.md. Source coverage concerns the
#: rendered body, not descriptive metadata such as name and description.
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL
)


class SourceCheck(BaseCheck):
    """Check source coverage, orphans, duplicates and dangling reference links."""

    check_id = "source-coverage"

    def _run(self, skill_dir: Path) -> list[Finding]:
        findings: list[Finding] = []
        provenance_path = skill_dir / "provenance.yml"

        provenance_exists = provenance_path.exists()
        declared: list[str] = []
        provenance_valid = True
        if provenance_exists:
            declared, provenance_valid = self._load_declared_sources(
                provenance_path, findings
            )

        # Scan SKILL.md + references/*.md for citations and reference links.
        cited: set[str] = set()
        reference_files_on_disk: set[str] = set()
        has_source_bearing_content = False

        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            skill_text = skill_md.read_text(encoding="utf-8")
            cited.update(self._extract_citations(skill_text))
            has_source_bearing_content = self._has_substantive_markdown(
                skill_text, strip_frontmatter=True
            )
            self._check_reference_links(
                skill_text, skill_dir, findings, reference_files_on_disk
            )

        references_dir = skill_dir / "references"
        if references_dir.is_dir():
            for ref_file in sorted(references_dir.glob("*.md")):
                rel = ref_file.relative_to(skill_dir).as_posix()
                reference_files_on_disk.add(rel)
                text = ref_file.read_text(encoding="utf-8")
                cited.update(self._extract_citations(text))
                has_source_bearing_content = (
                    has_source_bearing_content
                    or self._has_substantive_markdown(text)
                )

        # A ledger is mandatory once the Skill carries knowledge or source
        # references. Empty skeletons retain the historical warning for a
        # missing file, while a present-but-empty ledger is valid for them.
        if not provenance_exists:
            findings.append(
                Finding(
                    severity=(
                        CheckStatus.FAIL
                        if has_source_bearing_content or cited
                        else CheckStatus.WARN
                    ),
                    code="source.no_provenance",
                    location="provenance.yml",
                    message=(
                        "provenance.yml not found; source-bearing content "
                        "cannot be verified."
                    ),
                )
            )
        elif provenance_valid and not declared and (
            has_source_bearing_content or cited
        ):
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="source.empty_provenance",
                    location="provenance.yml",
                    message=(
                        "provenance.yml declares no sources for non-empty "
                        "knowledge or reference content."
                    ),
                )
            )

        declared_set: set[str] = set()
        for sid in declared:
            if sid in declared_set:
                findings.append(
                    Finding(
                        severity=CheckStatus.FAIL,
                        code="source.duplicate_id",
                        location="provenance.yml",
                        message=(
                            f"Duplicate source_id '{sid}' in provenance.yml."
                        ),
                    )
                )
            else:
                declared_set.add(sid)

        for sid in sorted(cited - declared_set):
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="source.undeclared",
                    location="SKILL.md/references",
                    message=(
                        f"Source '{sid}' is cited in content but not "
                        "declared in provenance.yml."
                    ),
                )
            )

        for sid in sorted(declared_set - cited):
            findings.append(
                Finding(
                    severity=CheckStatus.WARN,
                    code="source.orphan",
                    location="provenance.yml",
                    message=(
                        f"Source '{sid}' is declared but never cited in "
                        "any content file."
                    ),
                )
            )

        return findings

    @staticmethod
    def _load_declared_sources(
        provenance_path: Path, findings: list[Finding]
    ) -> tuple[list[str], bool]:
        """Return declared source IDs and whether the ledger structure is valid."""
        try:
            data = yaml.safe_load(provenance_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="source.provenance_malformed",
                    location="provenance.yml",
                    message=f"provenance.yml is not valid YAML: {exc}",
                )
            )
            return [], False

        if not isinstance(data, dict):
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="source.provenance_not_mapping",
                    location="provenance.yml",
                    message="provenance.yml top level must be a YAML mapping.",
                )
            )
            return [], False

        sources = data.get("sources") or []
        if not isinstance(sources, list):
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="source.provenance_sources_not_list",
                    location="provenance.yml",
                    message="provenance.yml 'sources' must be a list.",
                )
            )
            return [], False

        declared: list[str] = []
        for entry in sources:
            if isinstance(entry, dict) and "source_id" in entry:
                sid = entry["source_id"]
                if isinstance(sid, str):
                    declared.append(sid)
        return declared, True

    @staticmethod
    def _extract_citations(text: str) -> set[str]:
        """Return the set of source_ids cited in *text*."""
        return {m.group("source_id") for m in _SOURCE_CITATION_RE.finditer(text)}

    @staticmethod
    def _has_substantive_markdown(
        text: str, *, strip_frontmatter: bool = False
    ) -> bool:
        """Return whether Markdown contains content beyond structural headings."""
        content = _FRONTMATTER_RE.sub("", text, count=1) if strip_frontmatter else text
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line in {"---", "***", "___"}:
                continue
            if line.startswith("<!--") and line.endswith("-->"):
                continue
            return True
        return False

    @staticmethod
    def _check_reference_links(
        skill_text: str,
        skill_dir: Path,
        findings: list[Finding],
        reference_files_on_disk: set[str],
    ) -> None:
        """Flag ``references/<file>.md`` links in SKILL.md that are missing."""
        for match in _REFERENCE_FILE_RE.finditer(skill_text):
            rel = match.group(1)
            target = skill_dir / rel
            if not target.exists():
                findings.append(
                    Finding(
                        severity=CheckStatus.WARN,
                        code="links.missing_reference",
                        location=(
                            f"SKILL.md:"
                            f"{skill_text[:match.start()].count(chr(10)) + 1}"
                        ),
                        message=(
                            f"SKILL.md references '{rel}' but the file does "
                            "not exist."
                        ),
                    )
                )


__all__ = ["SourceCheck"]
