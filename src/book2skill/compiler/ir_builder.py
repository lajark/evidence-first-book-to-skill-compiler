"""Build a host-agnostic SkillIR from Schema-layer knowledge units.

The :class:`IRBuilder` is the "Normalize" stage of the pipeline
(ARCHITECTURE §3 step 6): it takes reviewed :class:`KnowledgeUnit` records
plus a :class:`SkillSpec` (the human-authored skill shape) and produces a
:class:`SkillIR` that conforms to ``schemas/skill-ir.schema.json``.

Splitting strategy (PRD FR-04 + SKILL_AUTHORING_STANDARD §3):

- Executable kinds (``framework``, ``technique``, ``decision_rule``,
  ``checklist``, ``principle``) carry the methodology skeleton and become
  concise, ordered ``workflow`` steps in the main file so a reader sees an
  executable sequence, not just a description.
- ``case`` / ``term`` / ``anti_pattern`` units are reference material; they
  sink into ``references/<kind>.md`` via :meth:`IRBuilder.build_references`.
- Every usable unit (including ones also rendered into the workflow) is
  additionally listed in ``references/provenance.md`` and rendered in its
  kind's reference file as a detailed view; the workflow is the concise
  ordered view, references is the deep-dive view.

The builder is pure (no I/O) so it can be unit-tested in isolation. Schema
conformance is enforced by Pydantic at construction; the optional
:func:`validate_skill_ir_against_schema` re-checks against the JSON Schema
using ``jsonschema`` (a dev dependency, used in tests).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.domain.knowledge import KnowledgeUnit, UnitKind
from book2skill.resources import schema_file

#: Name pattern shared by the Pydantic model and ``skill-ir.schema.json``.
_SKILL_NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

#: Kinds rendered as ordered, executable workflow steps in the main file.
#: ``case`` / ``term`` / ``anti_pattern`` are reference material and stay in
#: ``references/``; the order here controls how steps appear in SKILL.md:
#: framework opens, then techniques (the actionable core), decision rules,
#: checklists, and principles as constraints.
_WORKFLOW_KIND_ORDER: tuple[UnitKind, ...] = (
    UnitKind.FRAMEWORK,
    UnitKind.TECHNIQUE,
    UnitKind.DECISION_RULE,
    UnitKind.CHECKLIST,
    UnitKind.PRINCIPLE,
)

#: Kinds that sink to ``references/`` as detailed supporting material. This
#: still includes the executable kinds: the workflow is the concise ordered
#: view, the per-kind reference file is the detailed view for deep lookup.
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

# ``frozenset`` is useful for membership but its iteration order varies with
# ``PYTHONHASHSEED``. Keep every rendered reference route in this fixed order.
_REFERENCE_KIND_ORDER: tuple[UnitKind, ...] = (
    UnitKind.FRAMEWORK,
    UnitKind.PRINCIPLE,
    UnitKind.TECHNIQUE,
    UnitKind.CASE,
    UnitKind.TERM,
    UnitKind.ANTI_PATTERN,
    UnitKind.CHECKLIST,
    UnitKind.DECISION_RULE,
)

#: Mapping from kind to its plural slug used in reference file names.
_KIND_TO_PLURAL: dict[UnitKind, str] = {
    UnitKind.FRAMEWORK: "frameworks",
    UnitKind.PRINCIPLE: "principles",
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
    required_inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
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
    required_inputs: list[str] = Field(
        default_factory=lambda: [
            "A legally held source document or a verified analysis bundle."
        ]
    )
    outputs: list[str] = Field(
        default_factory=lambda: [
            "A concise, source-traceable response or action plan; never raw book text."
        ]
    )


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class IRBuilder:
    """Assemble a :class:`SkillIR` from knowledge units and a :class:`SkillSpec`.

    The builder filters out rejected/superseded units, splits the remainder
    into ordered workflow steps (executable kinds: framework, technique,
    decision_rule, checklist, principle) and reference material (case, term,
    anti_pattern), and emits an IR plus a reference-content map for the
    writer. Executable kinds also get a detailed entry in their per-kind
    reference file.
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
            # No executable kinds (framework/technique/decision_rule/checklist/
            # principle): synthesize a single routing step so the IR still
            # satisfies the schema's minItems=1 and the Skill has a usable
            # entry point.
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
                required_inputs=self._spec.required_inputs,
                outputs=self._spec.outputs,
                conditions=self._unique_items(
                    condition
                    for unit in usable
                    for condition in unit.conditions
                ),
                exceptions=self._unique_items(
                    exception
                    for unit in usable
                    for exception in unit.exceptions
                ),
                knowledge_refs=knowledge_refs,
                references=references,
                examples=self._build_examples(usable),
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
        Markdown content. Every usable unit is also listed in
        ``references/provenance.md`` so framework/principle evidence remains
        traceable even when it is rendered directly into ``SKILL.md``.
        """
        usable = self._usable_units()
        by_kind = self._group_by_kind(usable)
        result: dict[str, str] = {}
        if usable:
            result["references/provenance.md"] = self._render_provenance_reference(
                usable
            )
        for kind in _REFERENCE_KIND_ORDER:
            units = by_kind.get(kind)
            if units is None:
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
    def _unique_items(items: Any) -> list[str]:
        """Keep non-empty contract statements once, in first-seen order."""
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, str):
                continue
            normalized = item.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @staticmethod
    def _build_examples(units: list[KnowledgeUnit]) -> list[dict[str, Any]]:
        """Promote reviewed case units into concise, traceable examples."""
        examples: list[dict[str, Any]] = []
        for unit in units:
            kind = UnitKind(unit.kind) if isinstance(unit.kind, str) else unit.kind
            if kind != UnitKind.CASE:
                continue
            examples.append(
                {
                    "unit_id": unit.unit_id,
                    "scenario": unit.content,
                    "conditions": list(unit.conditions),
                    "exceptions": list(unit.exceptions),
                }
            )
        return examples

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
        """Turn executable kinds into concise, ordered workflow steps.

        Steps appear in ``_WORKFLOW_KIND_ORDER`` order so a reader sees the
        framework overview first, then the actionable techniques, decision
        rules, checklists, and principles as constraints. ``case`` / ``term``
        / ``anti_pattern`` are reference material and stay out of the workflow;
        a trailing routing step points at every reference file for those.
        """
        grouped = self._group_by_kind(units)
        steps: list[WorkflowStep] = []
        for kind in _WORKFLOW_KIND_ORDER:
            for u in grouped.get(kind, []):
                steps.append(
                    WorkflowStep(
                        step=self._summarize_step(kind, u),
                        description=self._workflow_description(u),
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

    @staticmethod
    def _workflow_description(unit: KnowledgeUnit) -> str:
        """Keep the Kernel concise while retaining full content in references."""
        compact = " ".join(
            line.strip() for line in unit.content.splitlines() if line.strip()
        )
        if len(compact) > 240:
            return compact[:237].rstrip() + "..."
        return compact

    def _reference_filenames(
        self, units: list[KnowledgeUnit]
    ) -> list[str]:
        """Return the relative paths of reference files that will be written."""
        grouped = self._group_by_kind(units)
        return [
            self._reference_filename(kind)
            for kind in _REFERENCE_KIND_ORDER
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
            lines.append(f"<!-- book2skill-unit-start: {u.unit_id} -->")
            lines.append(f"## {u.unit_id}")
            lines.append("")
            # Normalize embedded source line endings before outer Markdown
            # serialization. Otherwise Windows text translation can turn
            # ``\r\n`` into ``\r\r\n`` and reopen as an extra blank line.
            lines.append(u.content.replace("\r\n", "\n").replace("\r", "\n"))
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
            lines.append(f"<!-- book2skill-unit-end: {u.unit_id} -->")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_provenance_reference(units: list[KnowledgeUnit]) -> str:
        """Render a compact unit-to-source ledger for every usable unit."""
        lines = ["# Provenance", ""]
        for unit in units:
            lines.append(f"## {unit.unit_id}")
            lines.append("")
            lines.append("**Sources:**")
            for ref in unit.source_refs:
                lines.append(
                    f"- {ref.source_id} / {ref.block_id}"
                    + (f' — "{ref.quote}"' if ref.quote else "")
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# JSON Schema validation (used in tests; jsonschema is a dev dependency)
# ---------------------------------------------------------------------------


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

    schema = json.loads(schema_file("skill-ir.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(instance=ir.model_dump(mode="json"), schema=schema)


__all__ = [
    "SkillIR",
    "SkillSpec",
    "SkillUsage",
    "WorkflowStep",
    "IRBuilder",
    "validate_skill_ir_against_schema",
]
