"""Render a :class:`SkillIR` into a standard Skill directory on disk.

The :class:`SkillWriter` is the "Compile" stage (ARCHITECTURE §3 step 7). It
takes a host-agnostic :class:`SkillIR` plus the reference content produced by
:class:`~book2skill.compiler.ir_builder.IRBuilder` and writes the canonical
directory layout defined in ``templates/generated-skill/``:

::

    <output_dir>/
    ├── SKILL.md            # frontmatter + workflow + routing (budget-gated)
    ├── references/         # detailed units, one file per kind
    ├── assets/             # placeholder for templates/resources
    ├── provenance.yml      # source ledger stub (TASK-013 enriches)
    └── quality-report.md   # quality-gate stub (TASK-016 fills)

All files are written via :func:`~book2skill.storage.atomic_write` so a failure
never leaves a half-written Skill directory. The main ``SKILL.md`` is checked
against :class:`~book2skill.compiler.token_budget.TokenBudget`; exceeding the
hard ceiling raises :data:`~book2skill.domain.ErrorCode.BUILD_BUDGET_EXCEEDED`.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import yaml

from book2skill.compiler.ir_builder import SkillIR, WorkflowStep
from book2skill.compiler.token_budget import BudgetResult, TokenBudget, check_budget
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.domain.models import SourceManifest
from book2skill.resources import template_file
from book2skill.storage.file_storage import atomic_write

#: Default budget for the main SKILL.md (PRD FR-04: 2,500–5,000 tokens).
_DEFAULT_BUDGET = TokenBudget()

class SkillWriter:
    """Write a :class:`SkillIR` to a standard Skill directory.

    The writer is the only compiler component that touches the filesystem;
    :class:`IRBuilder` is pure. This keeps I/O at the boundary so the build
    logic stays testable without fixtures.
    """

    def __init__(
        self,
        output_dir: Path,
        budget: TokenBudget | None = None,
        templates_dir: Path | None = None,
    ) -> None:
        self._output_dir = output_dir.resolve()
        self._budget = budget or _DEFAULT_BUDGET
        self._templates_dir = templates_dir.resolve() if templates_dir else None

    def write(
        self,
        ir: SkillIR,
        references: dict[str, str] | None = None,
        source_ids: list[str] | None = None,
        source_manifests: list[SourceManifest] | None = None,
        wiki_files: dict[str, str] | None = None,
    ) -> Path:
        """Write the full Skill directory and return its path.

        Args:
            ir: The compiled SkillIR.
            references: Mapping of relative path (``references/foo.md``) to
                Markdown content, from :meth:`IRBuilder.build_references`.
            source_ids: Optional list of source_ids for the provenance stub.
                Used as a fallback when *source_manifests* is not provided;
                metadata (title/sha256) is marked ``unknown`` in that case.
            source_manifests: Optional list of :class:`SourceManifest` records
                used to populate provenance.yml with real ``content_sha256``
                and ``title`` (the latter falling back to ``original_name`` or
                ``source_id`` since SourceManifest does not carry a dedicated
                title field). ``author``/``edition`` remain ``unknown`` until
                a richer metadata source is introduced.

        Raises:
            DomainError: With :data:`ErrorCode.BUILD_BUDGET_EXCEEDED` when the
                rendered main file exceeds ``budget.hard_max``.
        """
        references = references or {}
        self._output_dir.mkdir(parents=True, exist_ok=True)

        skill_md = self._render_skill_md(ir)
        self._enforce_budget(skill_md, ir.name)

        atomic_write(self._output_dir / "SKILL.md", skill_md)

        for rel_path in sorted(references):
            content = references[rel_path]
            target = self._safe_target(rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target, content)

        # assets/ exists for structural completeness; TASK-019 fills it.
        (self._output_dir / "assets").mkdir(exist_ok=True)

        # Wiki layer derived views (P2): chapters/glossary/patterns/cheatsheet.
        for rel_path in sorted(wiki_files or {}):
            content = (wiki_files or {})[rel_path]
            target = self._safe_target(rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target, content)

        atomic_write(
            self._output_dir / "provenance.yml",
            self._render_provenance(ir, source_ids, source_manifests),
        )
        atomic_write(
            self._output_dir / "quality-report.md",
            self._render_quality_report(ir),
        )
        return self._output_dir

    # -- rendering --------------------------------------------------------

    def _render_skill_md(self, ir: SkillIR) -> str:
        """Render SKILL.md from the template, filled with IR content."""
        template_text = self._read_template("SKILL.md")
        title = ir.name.replace("-", " ").title()
        content = template_text.replace("<skill-name>", ir.name)
        # Serialize the free-form description as a YAML scalar. Bare values
        # containing ``:`` (or other YAML syntax) would otherwise make the
        # generated frontmatter invalid.
        description_yaml = yaml.safe_dump(
            {"description": ir.description},
            sort_keys=False,
            allow_unicode=True,
        ).removeprefix("description: ").rstrip()
        content = content.replace(
            "<What this skill does, when to use it, and key boundary.>",
            description_yaml,
        )
        content = content.replace("<Skill title>", title)

        content = content.replace(
            "- <trigger>", self._render_list(ir.usage.use_when, "- ")
        )
        content = content.replace(
            "- <boundary>",
            self._render_list(
                ir.usage.do_not_use_when, "- ", default="- Not applicable."
            ),
        )
        content = content.replace(
            "- <input>",
            self._render_list(
                ir.required_inputs,
                "- ",
                default="- No additional input requirements.",
            ),
        )
        content = content.replace(
            "1. <step>", self._render_workflow(ir.workflow)
        )
        content = content.replace(
            "- <output>",
            self._render_list(
                ir.outputs,
                "- ",
                default="- No output contract was declared.",
            ),
        )
        content = content.replace(
            "- <condition>",
            self._render_list(
                ir.conditions,
                "- ",
                default="- No additional conditions.",
            ),
        )
        content = content.replace(
            "- <exception>",
            self._render_list(
                ir.exceptions,
                "- ",
                default="- No additional exceptions.",
            ),
        )
        content = content.replace(
            "- <example>",
            self._render_examples(ir.examples),
        )

        # Append reference routing to the Evidence section when present.
        if ir.references:
            ref_line = (
                "- Detailed references: "
                + ", ".join(f"`{r}`" for r in ir.references)
            )
            content = content.rstrip() + "\n" + ref_line + "\n"
        return content

    def _render_provenance(
        self,
        ir: SkillIR,
        source_ids: list[str] | None,
        source_manifests: list[SourceManifest] | None = None,
    ) -> str:
        """Render provenance.yml.

        When *source_manifests* is provided, populate ``source_id`` /
        ``content_sha256`` / ``title`` / ``format`` / ``ingested_at`` from the
        real manifest records. ``title`` falls back to ``original_name`` then
        ``source_id`` because :class:`SourceManifest` does not carry a
        dedicated title field; ``author`` and ``edition`` remain ``unknown``
        until a richer metadata source is introduced. When *source_manifests*
        is ``None`` but *source_ids* is provided, fall back to the legacy
        stub behaviour (all fields ``unknown``). When both are empty, emit a
        placeholder line.

        Serialised with :func:`yaml.safe_dump` so quoting / escaping of
        special characters is handled by the YAML library rather than a
        bespoke emitter.
        """
        built_at = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
        data: dict[str, object] = {
            "schema_version": 1,
            "skill_name": ir.name,
            "built_at": built_at,
            "review_status": "candidate",
            "sources": [],
        }
        sources: list[dict[str, str]] = []
        if source_manifests:
            for m in sorted(source_manifests, key=lambda manifest: manifest.source_id):
                title = m.original_name or m.source_id
                ingested = m.ingested_at.isoformat(timespec="seconds")
                sources.append(
                    {
                        "source_id": m.source_id,
                        "title": title,
                        "author": "unknown",
                        "edition": "unknown",
                        "content_sha256": m.content_sha256,
                        "format": str(m.format),
                        "ingested_at": ingested,
                    }
                )
        elif source_ids:
            for sid in sorted(source_ids):
                sources.append(
                    {
                        "source_id": sid,
                        "title": "unknown",
                        "author": "unknown",
                        "edition": "unknown",
                        "content_sha256": "unknown",
                    }
                )
        data["sources"] = sources
        return yaml.safe_dump(
            data, default_flow_style=False, sort_keys=False, allow_unicode=True
        )

    def _render_quality_report(self, ir: SkillIR) -> str:
        """Render the quality-report.md stub (TASK-016 fills the checks)."""
        template_text = self._read_template("quality-report.md")
        # The template is a stub with placeholder values; keep its structure
        # and inject the skill name as the run id.
        return template_text.replace("<run-id>", ir.name)

    # -- helpers ----------------------------------------------------------

    def _read_template(self, name: str) -> str:
        """Load an override template or the embedded package resource."""
        if self._templates_dir is not None:
            return (self._templates_dir / name).read_text(encoding="utf-8")
        return template_file(name).read_text(encoding="utf-8")

    @staticmethod
    def _render_list(
        items: list[str], prefix: str, default: str | None = None
    ) -> str:
        """Render a list of items as prefixed lines.

        Returns *default* (or an empty string) when *items* is empty so the
        template placeholder is always replaced.
        """
        if not items:
            return default or ""
        return "\n".join(f"{prefix}{item}" for item in items)

    @staticmethod
    def _render_examples(examples: list[dict[str, object]]) -> str:
        """Render reviewed case units without exposing their source quotes."""
        rendered: list[str] = []
        for example in examples:
            scenario = example.get("scenario")
            unit_id = example.get("unit_id")
            if not isinstance(scenario, str) or not scenario.strip():
                continue
            suffix = f" (case: {unit_id})" if isinstance(unit_id, str) else ""
            rendered.append(f"- {scenario.strip()}{suffix}")
        return "\n".join(rendered) or "- No reviewed case example is available."

    @staticmethod
    def _render_workflow(steps: list[WorkflowStep]) -> str:
        """Render workflow steps as a numbered list with optional descriptions."""
        lines: list[str] = []
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step.step}")
            if step.description:
                lines.append(f"   - {step.description}")
        return "\n".join(lines)

    def _safe_target(self, rel_path: str) -> Path:
        """Resolve *rel_path* under the output dir, rejecting traversal."""
        target = (self._output_dir / rel_path).resolve()
        # self._output_dir is already resolved in __init__; use is_relative_to
        # (Python 3.9+) instead of a fragile string-prefix check.
        if not target.is_relative_to(self._output_dir):
            raise DomainError(
                code=ErrorCode.BUILD_IR_FAILED,
                input_id=rel_path,
                message=(
                    f"Reference path '{rel_path}' escapes the output "
                    "directory."
                ),
                recovery="Ensure reference paths are relative and do not use '..'.",
            )
        return target

    def _enforce_budget(self, skill_md: str, skill_name: str) -> None:
        """Raise BUILD_BUDGET_EXCEEDED if the main file is over the hard max."""
        result = check_budget(skill_md, self._budget)
        if result.exceeds_hard_max:
            raise DomainError(
                code=ErrorCode.BUILD_BUDGET_EXCEEDED,
                input_id=skill_name,
                message=(
                    f"Rendered SKILL.md is ~{result.tokens} tokens, exceeding "
                    f"the hard ceiling of {self._budget.hard_max}."
                ),
                recovery=(
                    "Move more detail into references/ files and rebuild; or "
                    "raise TokenBudget.hard_max if the ceiling is too strict."
                ),
                details={"tokens": result.tokens, "hard_max": self._budget.hard_max},
            )


# Re-export for convenience so callers can build a budget inline.
__all__ = ["SkillWriter", "TokenBudget", "BudgetResult", "check_budget"]
