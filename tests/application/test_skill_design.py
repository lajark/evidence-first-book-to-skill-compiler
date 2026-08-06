from __future__ import annotations

import json

from jsonschema import validate

from book2skill.application.models import AnalysisBundle, CandidateUnit, SuggestedSkill
from book2skill.application.skill_design import (
    FixtureKind,
    generate_skill_fixtures,
    review_skill_design,
)
from book2skill.compiler import SkillIR, SkillSpec, SkillUsage, WorkflowStep
from book2skill.resources import schema_file


def _spec() -> SkillSpec:
    return SkillSpec(
        name="bounded-method",
        description="Apply one bounded method when planning work.",
        use_when=["When a bounded plan is needed."],
        do_not_use_when=["When the task is unrelated."],
    )


def _ir(*, steps: int = 1) -> SkillIR:
    return SkillIR(
        name="bounded-method",
        description="Apply one bounded method when planning work.",
        usage=SkillUsage(
            use_when=["When a bounded plan is needed."],
            do_not_use_when=["When the task is unrelated."],
        ),
        workflow=[
            WorkflowStep(step=f"Step {index}", description="Execute it.", refs=["u-1"])
            for index in range(steps)
        ],
        knowledge_refs=["u-1"],
        references=["references/techniques.md"],
        exceptions=["Stop when required input is missing."],
        examples=[{"unit_id": "case-1", "content": "A short example."}],
    )


def _bundle() -> AnalysisBundle:
    return AnalysisBundle(
        collection_id="collection-1",
        source_ids=["src-1"],
        structure=[],
        candidate_units=[
            CandidateUnit(
                unit_id="u-1",
                kind="technique",
                content="Execute one method.",
                source_refs=[{"source_id": "src-1", "block_id": "b-1"}],
                confidence=0.9,
                review_status="approved",
            )
        ],
        review_queue=[],
        suggested_skills=[
            SuggestedSkill(
                name="bounded-method",
                description="Apply one bounded method when planning work.",
                rationale="Single task.",
                task_boundary="single_task",
                positive_trigger_examples=["Plan this bounded task."],
                negative_trigger_examples=["Translate this paragraph."],
            )
        ],
    )


def test_fixture_set_has_required_trigger_and_failure_cases() -> None:
    fixtures = generate_skill_fixtures(_bundle(), _spec(), _ir())
    kinds = [fixture.kind for fixture in fixtures.fixtures]

    assert kinds.count(FixtureKind.POSITIVE_TRIGGER) == 3
    assert kinds.count(FixtureKind.NEGATIVE_TRIGGER) == 3
    assert FixtureKind.MISSING_INPUT in kinds
    assert FixtureKind.CONFLICT in kinds
    assert FixtureKind.OUT_OF_SCOPE in kinds
    assert FixtureKind.OUTPUT_FORMAT in kinds

    schema = json.loads(
        schema_file("skill-fixtures.schema.json").read_text(encoding="utf-8")
    )
    validate(instance=fixtures.model_dump(mode="json"), schema=schema)


def test_design_review_is_advisory_and_flags_broad_workflows() -> None:
    review = review_skill_design(_bundle(), _spec(), _ir(steps=11))

    assert review.advisory_only is True
    assert "design.task_boundary.broad" in {item.code for item in review.findings}
    assert review.scores.task_focus < 5
    assert review.execution_contract.inputs[0].field_id == "input_1"
    assert review.execution_contract.outputs[0].field_id == "output_1"
    assert review.execution_contract.invariants

    schema = json.loads(
        schema_file("skill-design-review.schema.json").read_text(encoding="utf-8")
    )
    validate(instance=review.model_dump(mode="json"), schema=schema)


def test_design_review_flags_inferred_only_executable_evidence() -> None:
    bundle = _bundle().model_copy(deep=True)
    bundle.candidate_units[0].evidence_level = "inferred"
    bundle.candidate_units[0].evidence_note = "Derived during review."

    review = review_skill_design(bundle, _spec(), _ir())

    assert "design.evidence.inferred_only" in {
        item.code for item in review.findings
    }
