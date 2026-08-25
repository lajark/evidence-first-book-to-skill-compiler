"""Tests for the SkillIR builder."""

from __future__ import annotations

import pytest

from book2skill.compiler.ir_builder import (
    IRBuilder,
    SkillIR,
    SkillSpec,
    WorkflowStep,
    validate_skill_ir_against_schema,
)
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.domain.knowledge import (
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    UnitKind,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _ref(block_id: str = "blk-1") -> KnowledgeRef:
    return KnowledgeRef(source_id="src-1", block_id=block_id, quote="q")


def _unit(
    *,
    unit_id: str = "ku-1",
    kind: UnitKind = UnitKind.PRINCIPLE,
    content: str = "Always write tests before code.",
    review_status: KnowledgeStatus = KnowledgeStatus.APPROVED,
    conditions: list[str] | None = None,
    exceptions: list[str] | None = None,
) -> KnowledgeUnit:
    return KnowledgeUnit(
        unit_id=unit_id,
        kind=kind,
        content=content,
        conditions=conditions or [],
        exceptions=exceptions or [],
        source_refs=[_ref()],
        confidence=0.8,
        review_status=review_status,
    )


def _spec(
    *,
    name: str = "test-skill",
    description: str = "A test skill that compiles knowledge into a usable form.",
    use_when: list[str] | None = None,
    do_not_use_when: list[str] | None = None,
    required_inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> SkillSpec:
    return SkillSpec(
        name=name,
        description=description,
        use_when=use_when or ["You need to apply structured knowledge."],
        do_not_use_when=do_not_use_when or [],
        required_inputs=required_inputs
        or ["A verified brief with its intended audience."],
        outputs=outputs or ["A checked implementation plan."],
    )


# ---------------------------------------------------------------------------
# SkillSpec / SkillIR model validation
# ---------------------------------------------------------------------------


class TestSkillSpec:
    def test_valid_spec(self) -> None:
        spec = _spec()
        assert spec.name == "test-skill"

    def test_invalid_name_pattern_rejected(self) -> None:
        with pytest.raises(ValueError):
            _spec(name="Test_Skill")

    def test_name_uppercase_rejected(self) -> None:
        with pytest.raises(ValueError):
            _spec(name="TestSkill")

    def test_description_too_short(self) -> None:
        with pytest.raises(ValueError):
            _spec(description="too short")

    def test_description_too_long(self) -> None:
        with pytest.raises(ValueError):
            _spec(description="x" * 1025)

    def test_use_when_required(self) -> None:
        with pytest.raises(ValueError):
            SkillSpec(
                name="x", description="valid description here", use_when=[]
            )


class TestSkillIRModel:
    def test_valid_ir(self) -> None:
        ir = SkillIR(
            name="my-skill",
            description="A valid description for testing purposes.",
            usage={"use_when": ["x"], "do_not_use_when": []},
            workflow=[{"step": "Do something"}],
            knowledge_refs=["ku-1"],
        )
        assert ir.schema_version == 1
        assert ir.name == "my-skill"

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValueError):
            SkillIR(
                name="my-skill",
                description="A valid description for testing purposes.",
                usage={"use_when": ["x"], "do_not_use_when": []},
                workflow=[{"step": "x"}],
                rogue_field="bad",
            )


# ---------------------------------------------------------------------------
# IRBuilder
# ---------------------------------------------------------------------------


class TestIRBuilderBuild:
    def test_builds_ir_from_units(self) -> None:
        units = [
            _unit(
                unit_id="ku-1",
                kind=UnitKind.FRAMEWORK,
                content="Define goals first.",
            ),
            _unit(unit_id="ku-2", kind=UnitKind.TECHNIQUE, content="Use TDD."),
        ]
        ir = IRBuilder(units, _spec()).build()
        assert ir.name == "test-skill"
        assert "ku-1" in ir.knowledge_refs
        assert "ku-2" in ir.knowledge_refs

    def test_contract_collects_domain_io_conditions_exceptions_and_cases(
        self,
    ) -> None:
        units = [
            _unit(
                unit_id="pr-1",
                content="Validate the request before deciding.",
                conditions=["Only after rights are confirmed"],
                exceptions=["Escalate ambiguous rights claims"],
            ),
            _unit(
                unit_id="case-1",
                kind=UnitKind.CASE,
                content="A reviewed request produced a traceable response.",
                conditions=["Only after rights are confirmed"],
            ),
        ]

        ir = IRBuilder(
            units,
            _spec(
                required_inputs=["A verified brief with its intended audience."],
                outputs=["A checked implementation plan."],
            ),
        ).build()

        assert ir.required_inputs == ["A verified brief with its intended audience."]
        assert ir.outputs == ["A checked implementation plan."]
        assert ir.conditions == ["Only after rights are confirmed"]
        assert ir.exceptions == ["Escalate ambiguous rights claims"]
        assert ir.examples == [
            {
                "unit_id": "case-1",
                "scenario": "A reviewed request produced a traceable response.",
                "conditions": ["Only after rights are confirmed"],
                "exceptions": [],
            }
        ]

    def test_workflow_orders_executable_kinds(self) -> None:
        units = [
            _unit(
                unit_id="fw-1",
                kind=UnitKind.FRAMEWORK,
                content="Define goals first.",
            ),
            _unit(
                unit_id="pr-1",
                kind=UnitKind.PRINCIPLE,
                content="Tests come first.",
            ),
            _unit(unit_id="tc-1", kind=UnitKind.TECHNIQUE, content="Use TDD."),
        ]
        ir = IRBuilder(units, _spec()).build()
        # Executable kinds are ordered framework -> technique -> principle,
        # then a trailing routing step. Technique is no longer buried only in
        # references; the main workflow is an executable sequence.
        assert len(ir.workflow) == 4
        assert ir.workflow[0].step.startswith("Framework:")
        assert ir.workflow[1].step.startswith("Technique:")
        assert ir.workflow[2].step.startswith("Principle:")
        assert ir.workflow[3].step == "Consult detailed references"

    def test_workflow_description_is_bounded_and_references_keep_full_content(
        self,
    ) -> None:
        content = "First actionable line.\n" + ("Detailed evidence. " * 40)
        unit = _unit(unit_id="bounded-1", kind=UnitKind.TECHNIQUE, content=content)
        ir = IRBuilder([unit], _spec()).build()
        refs = IRBuilder([unit], _spec()).build_references()

        assert len(ir.workflow[0].description) <= 240
        assert content in refs["references/techniques.md"]

    def test_workflow_synthesized_when_no_executable_kind(self) -> None:
        # Only reference-material kinds (case/term/anti_pattern): no step can
        # be built, so a single routing step is synthesized.
        units = [
            _unit(unit_id="cs-1", kind=UnitKind.CASE, content="A case story.")
        ]
        ir = IRBuilder(units, _spec()).build()
        assert len(ir.workflow) == 1
        assert "references" in ir.workflow[0].step.lower()

    def test_workflow_orders_kinds_in_methodology_order(self) -> None:
        # Kinds appear in _WORKFLOW_KIND_ORDER regardless of input order:
        # framework, technique, decision_rule, checklist, principle.
        units = [
            _unit(unit_id="pr-1", kind=UnitKind.PRINCIPLE, content="Stay focused."),
            _unit(unit_id="fw-1", kind=UnitKind.FRAMEWORK, content="Overview."),
            _unit(unit_id="cl-1", kind=UnitKind.CHECKLIST, content="Check items."),
            _unit(unit_id="tc-1", kind=UnitKind.TECHNIQUE, content="Do work."),
            _unit(unit_id="dr-1", kind=UnitKind.DECISION_RULE, content="If X then Y."),
        ]
        ir = IRBuilder(units, _spec()).build()
        steps = [s.step for s in ir.workflow if not s.step.startswith("Consult")]
        assert [s.split(":", 1)[0] for s in steps] == [
            "Framework",
            "Technique",
            "Decision Rule",
            "Checklist",
            "Principle",
        ]

    def test_reference_material_kinds_excluded_from_workflow(self) -> None:
        # case/term/anti_pattern are reference material: they appear in
        # references but never as workflow steps.
        units = [
            _unit(unit_id="tc-1", kind=UnitKind.TECHNIQUE, content="A technique."),
            _unit(unit_id="cs-1", kind=UnitKind.CASE, content="A case."),
            _unit(unit_id="tm-1", kind=UnitKind.TERM, content="A term."),
            _unit(
                unit_id="ap-1",
                kind=UnitKind.ANTI_PATTERN,
                content="An anti-pattern.",
            ),
        ]
        ir = IRBuilder(units, _spec()).build()
        step_kinds = [s.step.split(":", 1)[0] for s in ir.workflow]
        assert "Technique" in step_kinds
        assert "Case" not in step_kinds
        assert "Term" not in step_kinds
        assert "Anti Pattern" not in step_kinds

    def test_references_list_only_for_detail_kinds(self) -> None:
        units = [
            _unit(unit_id="fw-1", kind=UnitKind.FRAMEWORK, content="Define goals."),
            _unit(unit_id="tc-1", kind=UnitKind.TECHNIQUE, content="Use TDD."),
            _unit(unit_id="tm-1", kind=UnitKind.TERM, content="TDD means test-driven."),
        ]
        ir = IRBuilder(units, _spec()).build()
        assert "references/techniques.md" in ir.references
        assert "references/terms.md" in ir.references
        assert "references/frameworks.md" in ir.references

    def test_reference_routes_are_stable_when_input_kind_order_changes(self) -> None:
        units = [
            _unit(unit_id="tm-1", kind=UnitKind.TERM, content="A term."),
            _unit(unit_id="tc-1", kind=UnitKind.TECHNIQUE, content="A technique."),
        ]

        forward = IRBuilder(units, _spec())
        reverse = IRBuilder(list(reversed(units)), _spec())

        assert forward.build().references == reverse.build().references
        assert list(forward.build_references()) == list(reverse.build_references())

    def test_rejected_units_filtered(self) -> None:
        units = [
            _unit(unit_id="ok-1", kind=UnitKind.TECHNIQUE, content="Use TDD."),
            _unit(
                unit_id="bad-1",
                kind=UnitKind.TECHNIQUE,
                content="Bad idea.",
                review_status=KnowledgeStatus.REJECTED,
            ),
        ]
        ir = IRBuilder(units, _spec()).build()
        assert "ok-1" in ir.knowledge_refs
        assert "bad-1" not in ir.knowledge_refs

    def test_superseded_units_filtered(self) -> None:
        units = [
            _unit(
                unit_id="old-1",
                kind=UnitKind.TECHNIQUE,
                content="Old way.",
                review_status=KnowledgeStatus.SUPERSEDED,
            ),
            _unit(unit_id="new-1", kind=UnitKind.TECHNIQUE, content="New way."),
        ]
        ir = IRBuilder(units, _spec()).build()
        assert "old-1" not in ir.knowledge_refs
        assert "new-1" in ir.knowledge_refs

    def test_no_usable_units_raises(self) -> None:
        units = [
            _unit(
                unit_id="bad-1",
                review_status=KnowledgeStatus.REJECTED,
            )
        ]
        with pytest.raises(DomainError) as exc:
            IRBuilder(units, _spec()).build()
        assert exc.value.code == ErrorCode.BUILD_IR_FAILED

    def test_empty_units_raises(self) -> None:
        with pytest.raises(DomainError) as exc:
            IRBuilder([], _spec()).build()
        assert exc.value.code == ErrorCode.BUILD_IR_FAILED


class TestIRBuilderReferences:
    def test_reference_content_contains_unit_content(self) -> None:
        units = [
            _unit(
                unit_id="tc-1",
                kind=UnitKind.TECHNIQUE,
                content="Use TDD for new code.",
                conditions=["When writing new modules"],
                exceptions=["Legacy code without test harness"],
            )
        ]
        refs = IRBuilder(units, _spec()).build_references()
        assert "references/techniques.md" in refs
        content = refs["references/techniques.md"]
        assert "Use TDD for new code." in content
        assert "When writing new modules" in content
        assert "Legacy code without test harness" in content
        assert "tc-1" in content

    def test_reference_includes_source_refs(self) -> None:
        units = [_unit(unit_id="tc-1", kind=UnitKind.TECHNIQUE)]
        refs = IRBuilder(units, _spec()).build_references()
        assert "src-1" in refs["references/techniques.md"]
        assert "blk-1" in refs["references/techniques.md"]

    def test_reference_normalizes_embedded_source_line_endings(self) -> None:
        units = [
            _unit(
                unit_id="tc-crlf",
                kind=UnitKind.TECHNIQUE,
                content="first\r\nsecond",
            )
        ]
        refs = IRBuilder(units, _spec()).build_references()

        assert "first\nsecond" in refs["references/techniques.md"]
        assert "first\r\nsecond" not in refs["references/techniques.md"]

    def test_all_units_get_a_provenance_reference(self) -> None:
        units = [_unit(unit_id="fw-1", kind=UnitKind.FRAMEWORK)]
        refs = IRBuilder(units, _spec()).build_references()
        assert set(refs) == {
            "references/provenance.md",
            "references/frameworks.md",
        }
        assert "fw-1" in refs["references/provenance.md"]
        assert "src-1 / blk-1" in refs["references/provenance.md"]
        assert "Always write tests before code." in refs["references/frameworks.md"]

    def test_anti_pattern_filename(self) -> None:
        units = [
            _unit(
                unit_id="ap-1",
                kind=UnitKind.ANTI_PATTERN,
                content="Don't copy-paste blindly.",
            )
        ]
        refs = IRBuilder(units, _spec()).build_references()
        assert "references/anti-patterns.md" in refs


class TestSchemaConformance:
    def test_built_ir_passes_json_schema(self) -> None:
        units = [
            _unit(unit_id="fw-1", kind=UnitKind.FRAMEWORK, content="Define goals."),
            _unit(unit_id="tc-1", kind=UnitKind.TECHNIQUE, content="Use TDD."),
            _unit(
                unit_id="tm-1",
                kind=UnitKind.TERM,
                content="TDD is test-driven dev.",
            ),
        ]
        ir = IRBuilder(units, _spec()).build()
        # Should not raise.
        validate_skill_ir_against_schema(ir)

    def test_workflow_step_is_object(self) -> None:
        step = WorkflowStep(step="Do X", description="Details", refs=["r"])
        dumped = step.model_dump(mode="json")
        assert isinstance(dumped, dict)
        assert dumped["step"] == "Do X"
