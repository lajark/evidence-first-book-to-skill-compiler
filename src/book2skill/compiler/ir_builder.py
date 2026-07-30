"""Build a host-agnostic SkillIR from Schema-layer knowledge units.

The :class:`IRBuilder` is the "Normalize" stage of the pipeline
(ARCHITECTURE §3 step 6): it takes reviewed :class:`KnowledgeUnit` records
plus a :class:`SkillSpec` (the human-authored skill shape) and produces a
:class:`SkillIR` that conforms to ``schemas/skill-ir.schema.json``.

Splitting strategy (PRD FR-04 + SKILL_AUTHORING_STANDARD §3):

- ``framework`` and ``principle`` units carry the methodology skeleton and
  become concise ``workflow`` steps in the main file.
- ``technique`` / ``case`` / ``term`` / ``anti_pattern`` / ``checklist`` /
  ``decision_rule`` units are detail; they sink into ``references/<kind>.md``
  via :meth:`IRBuilder.build_references`, keeping the main file within budget.

The builder is pure (no I/O) so it can be unit-tested in isolation. Schema
conformance is enforced by Pydantic at construction; the optional
:func:`validate_skill_ir_against_schema` re-checks against the JSON Schema
using ``jsonschema`` (a dev dependency, used in tests).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.domain.knowledge import KnowledgeUnit, UnitKind

#: Name pattern shared by the Pydantic model and ``skill-ir.schema.json``.
_SKILL_NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

#: Kinds that drive the main-file workflow (concise, methodology-level).
_WORKFLOW_KINDS: frozenset[UnitKind] = frozenset(
    {UnitKind.FRAMEWORK, UnitKind.PRINCIPLE}
)

#: Kinds that sink to ``references/`` as detailed supporting material.
_REFERENCE_KINDS: frozenset[UnitKind] = frozenset(
    {
        UnitKind.TECHNIQUE,
        UnitKind.CASE,
        UnitKind.TERM,
        UnitKind.ANTI_PATTERN,
        UnitKind.CHECKLIST,
        UnitKind.DECISION_RULE,
    }
)

#: Mapping from kind to its plural slug used in reference file names.
_KIND_TO_PLURAL: dict[UnitKind, str] = {
    UnitKind.TECHNIQUE: "techniques",
    UnitKind.CASE: "cases",
    UnitKind.TERM: "terms",
    UnitKind.ANTI_PATTERN: "anti-patterns",
    UnitKind.CHECKLIST: "checklists",
    UnitKind.DECISION_RULE: "decision-rules",
}

#: review statuses excluded from compilation. ``superseded`` records remain
#: in history but the latest non-superseded record is the current view;
#: ``rejected`` units are explicitly dropped by human review.
_EXCLUDED_STATUSES: frozenset[str] = frozenset({"rejected", "superseded"})


# ---------------------------------------------------------------------------
# Pydantic models mirroring schemas/skill-ir.schema.json
# ---------------------------------------------------------------------------


class SkillUsage(BaseModel):
    """The ``usage`` block: when to invoke the skill and when not to."""

    model_config = ConfigDict(extra="forbid")

    use_when: list[str] = Field(..., min_length=1)
    do_not_use_when: list[str] = Field(default_factory=list)


class WorkflowStep(BaseModel):
    """One step in the Skill workflow.

    The JSON Schema only requires workflow items to be objects; this model is
    a stricter, typed view (``step`` is required) that remains schema-conformant
    because every instance still serialises to a plain object.
    """

    model_config = ConfigDict(extra="allow")

    step: str = Field(..., min_length=1)
    description: str | None = None
    refs: list[str] = Field(default_factory=list)


class SkillIR(BaseModel):
    """Host-agnostic intermediate representation of a Skill.

    Conforms to ``schemas/skill-ir.schema.json``. This is the single
    cross-host semantic source (ARCHITECTURE §4): host adapters only translate
    paths/frontmatter, never knowledge content.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    name: str = Field(..., pattern=_SKILL_NAME_PATTERN, max_length=64)
    description: str = Field(..., min_length=10, max_length=1024)
    usage: SkillUsage
    workflow: list[WorkflowStep] = Field(..., min_length=1)
    knowledge_refs: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or v != v.strip():
            raise ValueError("name must not be empty or surrounded by whitespace")
        return v


# ---------------------------------------------------------------------------
# Input spec (human-authored skill shape)
# ---------------------------------------------------------------------------


class SkillSpec(BaseModel):
    """Human-authored description of the Skill to build.

    Provided by the caller (CLI or build use case). ``name`` must satisfy the
    SkillIR name pattern; ``description`` must state what the skill does, when
    to use it and when not to (PRD FR-04).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., pattern=_SKILL_NAME_PATTERN, max_length=64)
    description: str = Field(..., min_length=10, max_length=1024)
    use_when: list[str] = Field(..., min_length=1)
    do_not_use_when: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class IRBuilder:
    """Assemble a :class:`SkillIR` from knowledge units and a :class:`SkillSpec`.

    The builder filters out rejected/superseded units, splits the remainder
    into workflow (framework/principle) and references (detail kinds), and
    emits an IR plus a reference-content map for the writer.
    """

    def __init__(self, units: list[KnowledgeUnit], spec: SkillSpec) -> None:
        self._units = units
        self._spec = spec

    def build(self) -> SkillIR:
        """Return the compiled :class:`SkillIR`.

        Raises :class:`DomainError` with :data:`ErrorCode.BUILD_IR_FAILED` when
        no usable units remain after filtering, or when the spec produces an
        invalid IR.
        """
        usable = self._usable_units()
        if not usable:
            raise DomainError(
                code=ErrorCode.BUILD_IR_FAILED,
                input_id=self._spec.name,
                message=(
                    "Cannot build SkillIR: no usable knowledge units remain "
                    "after filtering out rejected/superseded records."
                ),
                recovery=(
                    "Mark at least one unit as reviewed/approved, or supply "
                    "additional units."
                ),
            )

        workflow = self._build_workflow(usable)
        if not workflow:
            # No framework/principle units: synthesize a single routing step so
            # the IR still satisfies the schema's minItems=1 and the Skill has
            # a usable entry point.
            workflow = [
                WorkflowStep(
                    step="Apply knowledge from references",
                    description=(
                        "No framework/principle units were provided; consult "
                        "the reference files for actionable detail."
                    ),
                    refs=self._reference_filenames(usable),
                )
            ]

        references = self._reference_filenames(usable)
        knowledge_refs = [u.unit_id for u in usable]

        try:
            return SkillIR(
                name=self._spec.name,
                description=self._spec.description,
                usage=SkillUsage(
                    use_when=self._spec.use_when,
                    do_not_use_when=self._spec.do_not_use_when,
                ),
                workflow=workflow,
                knowledge_refs=knowledge_refs,
                references=references,
            )
        except ValueError as exc:
            raise DomainError(
                code=ErrorCode.BUILD_IR_FAILED,
                input_id=self._spec.name,
                message=f"Invalid SkillIR: {exc}",
                recovery="Fix the SkillSpec and re-run the compiler.",
            ) from exc

    def build_references(self) -> dict[str, str]:
        """Render the ``references/<kind>.md`` content for detail units.

        Returns a mapping of relative path (``references/techniques.md``) to
        Markdown content. Only kinds with at least one unit produce a file.
        Empty when all units are framework/principle.
        """
        usable = self._usable_units()
        by_kind = self._group_by_kind(usable)
        result: dict[str, str] = {}
        for kind, units in by_kind.items():
            if kind not in _REFERENCE_KINDS:
                continue
            filename = self._reference_filename(kind)
            result[filename] = self._render_reference(kind, units)
        return result

    # -- internal helpers -------------------------------------------------

    def _usable_units(self) -> list[KnowledgeUnit]:
        """Return units not rejected/superseded, preserving input order."""
        return [
            u
            for u in self._units
            if str(u.review_status) not in _EXCLUDED_STATUSES
        ]

    @staticmethod
    def _group_by_kind(
        units: list[KnowledgeUnit],
    ) -> dict[UnitKind, list[KnowledgeUnit]]:
        grouped: dict[UnitKind, list[KnowledgeUnit]] = {}
        for u in units:
            kind = UnitKind(u.kind) if isinstance(u.kind, str) else u.kind
            grouped.setdefault(kind, []).append(u)
        return grouped

    def _build_workflow(
        self, units: list[KnowledgeUnit]
    ) -> list[WorkflowStep]:
        """Turn framework/principle units into concise workflow steps."""
        grouped = self._group_by_kind(units)
        steps: list[WorkflowStep] = []
        for kind in (UnitKind.FRAMEWORK, UnitKind.PRINCIPLE):
            for u in grouped.get(kind, []):
                steps.append(
                    WorkflowStep(
                        step=self._summarize_step(kind, u),
                        description=u.content,
                        refs=[u.unit_id],
                    )
                )
        if steps:
            # Append a routing step pointing at the detail references.
            ref_files = self._reference_filenames(units)
            if ref_files:
                steps.append(
                    WorkflowStep(
                        step="Consult detailed references",
                        description=(
                            "Load the matching reference file for techniques, "
                            "terms, cases and checklists before acting."
                        ),
                        refs=ref_files,
                    )
                )
        return steps

    @staticmethod
    def _summarize_step(kind: UnitKind, unit: KnowledgeUnit) -> str:
        """Produce a one-line workflow step label for a framework/principle."""
        label = kind.value.replace("_", " ").title()
        # Use the first line / first ~80 chars of content as the step summary.
        first_line = unit.content.strip().splitlines()[0] if unit.content else ""
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        return f"{label}: {first_line}" if first_line else label

    def _reference_filenames(
        self, units: list[KnowledgeUnit]
    ) -> list[str]:
        """Return the relative paths of reference files that will be written."""
        grouped = self._group_by_kind(units)
        return [
            self._reference_filename(kind)
            for kind in _REFERENCE_KINDS
            if kind in grouped
        ]

    @staticmethod
    def _reference_filename(kind: UnitKind) -> str:
        """Map a kind to its reference file path (plural form)."""
        return f"references/{_KIND_TO_PLURAL[kind]}.md"

    @staticmethod
    def _render_reference(
        kind: UnitKind, units: list[KnowledgeUnit]
    ) -> str:
        """Render a single ``references/<kind>.md`` file as Markdown."""
        title = kind.value.replace("_", " ").title()
        lines = [f"# {title}", ""]
        for u in units:
            lines.append(f"## {u.unit_id}")
            lines.append("")
            lines.append(u.content)
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
            if u.source_refs:
                lines.append("")
                lines.append("**Sources:**")
                for ref in u.source_refs:
                    lines.append(
                        f"- {ref.source_id} / {ref.block_id}"
                        + (f" — \"{ref.quote}\"" if ref.quote else "")
                    )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# JSON Schema validation (used in tests; jsonschema is a dev dependency)
# ---------------------------------------------------------------------------


def _schema_path() -> Path:
    """Locate ``schemas/skill-ir.schema.json`` relative to this module."""
    # compiler/ir_builder.py -> book2skill/ -> src/ -> project root.
    project_root = Path(__file__).resolve().parents[2].parent
    return project_root / "schemas" / "skill-ir.schema.json"


def validate_skill_ir_against_schema(ir: SkillIR) -> None:
    """Validate *ir* against ``schemas/skill-ir.schema.json``.

    Imports ``jsonschema`` lazily so the compiler has no runtime dependency on
    it; Pydantic enforces the same constraints at construction. Intended for
    use in the test suite to guard against schema drift.
    """
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - dev dependency guard
        raise RuntimeError(
            "jsonschema is required for schema validation but is not "
            "installed; it is listed in the 'dev' optional dependency group."
        ) from exc

    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(instance=ir.model_dump(mode="json"), schema=schema)


__all__ = [
    "SkillIR",
    "SkillSpec",
    "SkillUsage",
    "WorkflowStep",
    "IRBuilder",
    "validate_skill_ir_against_schema",
]
