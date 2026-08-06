"""Advisory-only Skill task-boundary review and deterministic fixtures."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from book2skill.application.models import AnalysisBundle
from book2skill.compiler import SkillIR, SkillSpec
from book2skill.storage.file_storage import atomic_write

SKILL_DESIGN_REVIEW_FILENAME = "skill-design-review.json"
SKILL_FIXTURES_FILENAME = "skill-fixtures.json"
_TOKEN_RE = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)


class DesignSeverity(StrEnum):
    ADVISORY = "advisory"
    WARNING = "warning"


class DesignFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: DesignSeverity
    message: str
    related_ids: list[str] = Field(default_factory=list)


class SkillDesignScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_precision: int = Field(..., ge=0, le=5)
    task_focus: int = Field(..., ge=0, le=5)
    workflow_executability: int = Field(..., ge=0, le=5)
    progressive_disclosure: int = Field(..., ge=0, le=5)
    deterministic_tooling: int = Field(..., ge=0, le=5)
    failure_boundary: int = Field(..., ge=0, le=5)
    example_quality: int = Field(..., ge=0, le=5)


class ContractField(BaseModel):
    """A typed input or output at the generated Skill boundary."""

    model_config = ConfigDict(extra="forbid")

    field_id: str
    value_type: Literal["string"] = "string"
    required: bool
    description: str


class ExecutionContract(BaseModel):
    """Deterministic execution boundary used by fixture assertions."""

    model_config = ConfigDict(extra="forbid")

    inputs: list[ContractField]
    outputs: list[ContractField]
    preconditions: list[str]
    invariants: list[str]
    failure_boundaries: list[str]
    escalation_paths: list[str]


class SkillDesignReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    skill_name: str
    advisory_only: Literal[True] = True
    scores: SkillDesignScores
    execution_contract: ExecutionContract
    findings: list[DesignFinding]


class FixtureKind(StrEnum):
    POSITIVE_TRIGGER = "positive_trigger"
    NEGATIVE_TRIGGER = "negative_trigger"
    NORMAL_EXECUTION = "normal_execution"
    MISSING_INPUT = "missing_input"
    CONFLICT = "conflict"
    OUT_OF_SCOPE = "out_of_scope"
    OUTPUT_FORMAT = "output_format"


class SkillFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_id: str
    kind: FixtureKind
    prompt: str
    should_trigger: bool
    assertions: list[str] = Field(..., min_length=1)


class SkillFixtureSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    fixture_set_id: str = Field(..., min_length=16)
    skill_name: str
    fixtures: list[SkillFixture]


def review_skill_design(
    bundle: AnalysisBundle | None, spec: SkillSpec, ir: SkillIR
) -> SkillDesignReview:
    """Return deterministic, non-mutating design diagnostics."""
    findings: list[DesignFinding] = []
    if len(ir.workflow) > 10 or _looks_multi_task(spec.description):
        findings.append(
            DesignFinding(
                code="design.task_boundary.broad",
                severity=DesignSeverity.WARNING,
                message=(
                    "The candidate may contain multiple independent tasks; "
                    "review a router/sub-skill split before publication."
                ),
            )
        )

    suggestions = sorted(
        bundle.suggested_skills if bundle is not None else [],
        key=lambda item: item.name,
    )
    for index, left in enumerate(suggestions):
        for right in suggestions[index + 1 :]:
            if _jaccard(_tokens(left.description), _tokens(right.description)) >= 0.6:
                findings.append(
                    DesignFinding(
                        code="design.candidates.overlap",
                        severity=DesignSeverity.WARNING,
                        message=(
                            "Suggested Skills have highly overlapping descriptions."
                        ),
                        related_ids=[left.name, right.name],
                    )
                )

    actionable = {"framework", "technique", "decision_rule", "checklist"}
    kinds = (
        {candidate.kind for candidate in bundle.candidate_units}
        if bundle is not None
        else set()
    )
    if kinds and not kinds.intersection(actionable):
        findings.append(
            DesignFinding(
                code="design.workflow.abstract_only",
                severity=DesignSeverity.WARNING,
                message=(
                    "Candidates contain principles/references but no executable method."
                ),
            )
        )
    if "case" in kinds and not ir.examples:
        findings.append(
            DesignFinding(
                code="design.case.unmapped",
                severity=DesignSeverity.ADVISORY,
                message=(
                    "Case evidence is present but is not mapped to a workflow example."
                ),
            )
        )

    if bundle is not None:
        executable = [
            candidate
            for candidate in bundle.candidate_units
            if candidate.kind in actionable
        ]
        if executable and all(
            candidate.evidence_level in {"inferred", "user_added"}
            for candidate in executable
        ):
            findings.append(
                DesignFinding(
                    code="design.evidence.inferred_only",
                    severity=DesignSeverity.WARNING,
                    message=(
                        "Executable rules are supported only by inferred or "
                        "user-added evidence; add primary or secondary support."
                    ),
                    related_ids=[candidate.unit_id for candidate in executable],
                )
            )

    scores = SkillDesignScores(
        trigger_precision=min(
            5, 2 + bool(spec.use_when) + bool(spec.do_not_use_when) * 2
        ),
        task_focus=max(
            0,
            5
            - int(any(f.code == "design.task_boundary.broad" for f in findings)) * 2,
        ),
        workflow_executability=min(
            5,
            2
            + int(bool(ir.workflow))
            + int(all(step.refs for step in ir.workflow)) * 2,
        ),
        progressive_disclosure=min(5, 2 + int(bool(ir.references)) * 3),
        deterministic_tooling=min(5, 2 + int(bool(ir.assets)) * 2),
        failure_boundary=min(
            5, 1 + int(bool(spec.do_not_use_when)) * 2 + int(bool(ir.exceptions)) * 2
        ),
        example_quality=min(5, 1 + min(4, len(ir.examples))),
    )
    return SkillDesignReview(
        skill_name=spec.name,
        scores=scores,
        execution_contract=_execution_contract(spec, ir),
        findings=findings,
    )


def generate_skill_fixtures(
    bundle: AnalysisBundle | None, spec: SkillSpec, ir: SkillIR
) -> SkillFixtureSet:
    """Generate the minimum trigger and execution comparison corpus."""
    suggested_positive = [
        example
        for suggestion in (bundle.suggested_skills if bundle is not None else [])
        for example in suggestion.positive_trigger_examples
    ]
    suggested_negative = [
        example
        for suggestion in (bundle.suggested_skills if bundle is not None else [])
        for example in suggestion.negative_trigger_examples
    ]
    positives = _at_least_three(
        suggested_positive or spec.use_when,
        fallbacks=[
            f"Use {spec.name} to complete its documented workflow.",
            f"Apply {spec.name} to a concrete, in-scope task.",
            f"Help me execute the {spec.name} method step by step.",
        ],
    )
    negatives = _at_least_three(
        suggested_negative or spec.do_not_use_when,
        fallbacks=[
            "Reproduce the source document verbatim.",
            "Answer an unrelated general-knowledge question.",
            "Act outside the documented usage boundary without confirmation.",
        ],
    )
    fixtures: list[SkillFixture] = []
    for index, prompt in enumerate(positives[:3], 1):
        fixtures.append(
            _fixture(
                f"PT-{index}",
                FixtureKind.POSITIVE_TRIGGER,
                prompt,
                True,
                ["skill_selected", "workflow_started"],
            )
        )
    for index, prompt in enumerate(negatives[:3], 1):
        fixtures.append(
            _fixture(
                f"NT-{index}",
                FixtureKind.NEGATIVE_TRIGGER,
                prompt,
                False,
                ["skill_not_selected"],
            )
        )
    fixtures.extend(
        [
            _fixture(
                "EX-1",
                FixtureKind.NORMAL_EXECUTION,
                positives[0],
                True,
                ["required_inputs_checked", "documented_output_returned"],
            ),
            _fixture(
                "MI-1",
                FixtureKind.MISSING_INPUT,
                f"Run {spec.name}, but I have not supplied the required input.",
                True,
                ["missing_input_reported", "no_fabrication"],
            ),
            _fixture(
                "CF-1",
                FixtureKind.CONFLICT,
                f"Run {spec.name} when two cited methods conflict.",
                True,
                ["conflict_preserved", "no_silent_resolution"],
            ),
            _fixture(
                "OS-1",
                FixtureKind.OUT_OF_SCOPE,
                negatives[0],
                False,
                ["usage_boundary_enforced"],
            ),
            _fixture(
                "OF-1",
                FixtureKind.OUTPUT_FORMAT,
                f"Run {spec.name} and return only its documented output contract.",
                True,
                ["output_contract_satisfied"],
            ),
        ]
    )
    identity = {
        "schema_version": 1,
        "skill_name": spec.name,
        "fixtures": [item.model_dump(mode="json") for item in fixtures],
        "ir_knowledge_refs": sorted(ir.knowledge_refs),
    }
    fixture_set_id = hashlib.sha256(_canonical_json(identity)).hexdigest()
    return SkillFixtureSet(
        fixture_set_id=fixture_set_id,
        skill_name=spec.name,
        fixtures=fixtures,
    )


def write_skill_design_artifacts(
    skill_dir: Path,
    review: SkillDesignReview,
    fixtures: SkillFixtureSet,
) -> tuple[Path, Path]:
    """Write advisory review and fixture set atomically."""
    review_path = Path(skill_dir) / SKILL_DESIGN_REVIEW_FILENAME
    fixtures_path = Path(skill_dir) / SKILL_FIXTURES_FILENAME
    atomic_write(review_path, review.model_dump_json(indent=2))
    atomic_write(fixtures_path, fixtures.model_dump_json(indent=2))
    return review_path, fixtures_path


def _fixture(
    fixture_id: str,
    kind: FixtureKind,
    prompt: str,
    should_trigger: bool,
    assertions: list[str],
) -> SkillFixture:
    return SkillFixture(
        fixture_id=fixture_id,
        kind=kind,
        prompt=prompt,
        should_trigger=should_trigger,
        assertions=assertions,
    )


def _execution_contract(spec: SkillSpec, ir: SkillIR) -> ExecutionContract:
    return ExecutionContract(
        inputs=[
            ContractField(
                field_id=f"input_{index}",
                required=True,
                description=description,
            )
            for index, description in enumerate(
                ir.required_inputs or spec.required_inputs, 1
            )
        ],
        outputs=[
            ContractField(
                field_id=f"output_{index}",
                required=True,
                description=description,
            )
            for index, description in enumerate(ir.outputs or spec.outputs, 1)
        ],
        preconditions=sorted(set(ir.conditions)),
        invariants=[
            "Preserve source traceability for knowledge-backed claims.",
            "Do not fabricate missing inputs, sources, or conflict resolution.",
            "Do not reproduce protected source text beyond configured limits.",
        ],
        failure_boundaries=sorted(set(ir.exceptions + spec.do_not_use_when)),
        escalation_paths=[
            "Ask the user for missing required input.",
            "Escalate unresolved evidence conflicts for explicit review.",
        ],
    )


def _at_least_three(values: list[str], *, fallbacks: list[str]) -> list[str]:
    result = [value.strip() for value in values if value.strip()]
    for fallback in fallbacks:
        if fallback not in result:
            result.append(fallback)
        if len(result) >= 3:
            break
    return result


def _looks_multi_task(description: str) -> bool:
    lowered = description.casefold()
    markers = (" and also ", "; and ", "以及", "同时完成", "多个独立")
    return any(marker in lowered for marker in markers)


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(text)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


__all__ = [
    "SKILL_DESIGN_REVIEW_FILENAME",
    "SKILL_FIXTURES_FILENAME",
    "DesignFinding",
    "DesignSeverity",
    "ContractField",
    "ExecutionContract",
    "FixtureKind",
    "SkillDesignReview",
    "SkillDesignScores",
    "SkillFixture",
    "SkillFixtureSet",
    "generate_skill_fixtures",
    "review_skill_design",
    "write_skill_design_artifacts",
]
