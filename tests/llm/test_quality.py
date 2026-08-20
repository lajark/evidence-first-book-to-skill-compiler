"""Quality gate, source-replayability, budget and authorization tests (OPT-P1-10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from book2skill.application.models import (
    AnalysisBundle,
    CandidateUnit,
    ConflictRecord,
    ReviewItem,
    StructureEntry,
)
from book2skill.llm.benchmark_slots import BenchmarkSlot
from book2skill.llm.quality import (
    MockReviewer,
    QualityBudget,
    QualityGate,
    QualityService,
    ReviewPatch,
    build_evidence_card,
)
from book2skill.llm.runtime import AnalysisRunManifest


def _unit(
    unit_id: str,
    *,
    confidence: float = 0.5,
    content: str = "A useful and actionable rule.",
) -> CandidateUnit:
    return CandidateUnit(
        unit_id=unit_id,
        kind="principle",
        content=content,
        conditions=["when needed"],
        exceptions=["unless"],
        source_refs=[{"source_id": "s1", "block_id": "b1", "quote": "A useful rule."}],
        confidence=confidence,
        review_status="candidate",
    )


def _bundle(
    *units: CandidateUnit, conflicts: list[ConflictRecord] | None = None
) -> AnalysisBundle:
    return AnalysisBundle(
        collection_id="c1",
        source_ids=["s1"],
        structure=[
            StructureEntry(
                block_id="b1", locator={}, heading="H", level=1, text_preview="H"
            )
        ],
        candidate_units=list(units),
        review_queue=[
            ReviewItem(
                item_id="r1",
                ref_type="candidate_unit",
                ref_id="u1",
                reason="x",
                severity="info",
            )
        ],
        conflicts=conflicts or [],
        analysis_run=AnalysisRunManifest(
            provider="mock",
            model="mock-rule-based-v1",
            prompt_version="analysis-v4",
            response_schema_version="analysis-response-v2",
            parameters={},
            fallback_allowed=False,
        ),
    )


class TestQualityGate:
    def test_clean_bundle_returns_no_flags(self) -> None:
        bundle = _bundle(_unit("u1", confidence=0.9))
        assert QualityGate.detect(bundle) == []

    def test_low_confidence_flagged(self) -> None:
        bundle = _bundle(_unit("u1", confidence=0.1))
        flags = QualityGate.detect(bundle)
        assert any(f.reason == "low_confidence" for f in flags)

    def test_conflict_flagged(self) -> None:
        conflict = ConflictRecord(
            conflict_id="x1", unit_ids=["u1", "u2"], description="d", status="open"
        )
        bundle = _bundle(_unit("u1"), _unit("u2"), conflicts=[conflict])
        assert any(f.reason == "conflict" for f in QualityGate.detect(bundle))

    def test_anomalous_content_flagged(self) -> None:
        bundle = _bundle(_unit("u1", content="short"))
        assert any(f.reason == "anomalous" for f in QualityGate.detect(bundle))


class TestQualityGateBenchmarkGap:
    def test_missing_slot_flagged(self) -> None:
        slots = [
            BenchmarkSlot(
                slot_id="A1",
                rubric_item_id="A1",
                kinds=["principle"],
                keywords=["谋攻"],
                min_count=1,
            )
        ]
        bundle = _bundle(_unit("u1", content="unrelated principle text."))
        flags = QualityGate.detect(bundle, benchmark_slots=slots)
        assert any(f.reason == "benchmark_gap" and f.unit_id == "A1" for f in flags)

    def test_covered_slot_not_flagged(self) -> None:
        slots = [
            BenchmarkSlot(
                slot_id="A1",
                rubric_item_id="A1",
                kinds=["principle"],
                keywords=["谋攻"],
                min_count=1,
            )
        ]
        bundle = _bundle(_unit("u1", content="谋攻为上，不战而屈人之兵。"))
        flags = QualityGate.detect(bundle, benchmark_slots=slots)
        assert not any(f.reason == "benchmark_gap" for f in flags)

    def test_no_slots_means_no_benchmark_flags(self) -> None:
        bundle = _bundle(_unit("u1", content="a normal candidate."))
        flags = QualityGate.detect(bundle)
        assert not any(f.reason == "benchmark_gap" for f in flags)


class TestEvidenceCardAnonymity:
    def test_card_hides_first_round_model_and_has_no_prompt(self) -> None:
        bundle = _bundle(_unit("u1"))
        card = build_evidence_card(bundle.candidate_units[0], bundle)
        dumped = card.model_dump()
        assert "mock-rule-based-v1" not in json.dumps(dumped)
        assert "prompt" not in dumped
        assert "endpoint" not in dumped
        assert card.anonymized_model_id  # redacted opaque id present


class TestReviewPatchReplayability:
    def test_unknown_source_ref_rejected(self) -> None:
        unit = _unit("u1")
        patch = ReviewPatch(
            unit_id="u1",
            disposition="merge",
            revised_content="revised",
            rationale="r",
            source_refs=[{"source_id": "s1", "block_id": "b999", "quote": "x"}],
            model_id="m",
        )
        # The replayability check is applied at the service boundary, not at
        # model construction: an unknown ref fails references_known(unit).
        assert patch.references_known(unit) is False

    def test_mock_reviewer_patch_is_replayable(self) -> None:
        unit = _unit("u1")
        reviewer = MockReviewer()
        patch = reviewer.review(build_evidence_card(unit, _bundle(unit)), unit)
        assert patch.references_known(unit)

    def test_merge_requires_revised_content(self) -> None:
        with pytest.raises(ValidationError):
            ReviewPatch(
                unit_id="u1",
                disposition="merge",
                rationale="r",
                source_refs=[],
                model_id="m",
            )


class TestQualityService:
    def test_clean_bundle_zero_extra_calls(self) -> None:
        service = QualityService(
            critic_reviewer=MockReviewer(),
            arbiter_reviewer=MockReviewer(),
        )
        result = service.review(_bundle(_unit("u1", confidence=0.9)))
        assert result.patches == []
        assert result.budget_exceeded is False

    def test_flagged_unit_produces_replayable_patch(self) -> None:
        service = QualityService(
            critic_reviewer=MockReviewer(),
            arbiter_reviewer=MockReviewer(),
        )
        result = service.review(_bundle(_unit("u1", confidence=0.1)))
        assert result.patches
        unit = _bundle(_unit("u1", confidence=0.1)).candidate_units[0]
        assert result.patches[0].references_known(unit)

    def test_budget_exceeded_stops_review(self) -> None:
        budget = QualityBudget(max_calls=1)
        service = QualityService(
            critic_reviewer=MockReviewer(),
            arbiter_reviewer=MockReviewer(),
            budget=budget,
        )
        result = service.review(
            _bundle(_unit("u1", confidence=0.1), _unit("u2", confidence=0.1))
        )
        assert result.budget_exceeded is True
        assert len(result.patches) < 2

    def test_arbiter_decision_updates_review_status(self) -> None:
        from book2skill.application.analyze import _apply_quality_review

        service = QualityService(
            critic_reviewer=MockReviewer(),
            arbiter_reviewer=MockReviewer(),
        )
        bundle = _bundle(_unit("u1", confidence=0.1))
        revised = _apply_quality_review(bundle, service)
        assert revised.quality_review is not None
        assert revised.quality_review
        assert revised.candidate_units[0].review_status == "reviewed"


class TestCli:
    def test_maximum_without_authorization_rejected(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from book2skill.cli import app

        path = tmp_path / "profiles.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "default_profile": "arb",
                    "profiles": [
                        {
                            "profile_id": "arb",
                            "provider": "mock",
                            "model": "mock-rule-based-v1",
                            "api_key_env": "MOCK_PLACEHOLDER",
                            "roles": ["map", "critic", "arbiter", "synthesis", "skill"],
                        },
                        {
                            "profile_id": "critic",
                            "provider": "mock",
                            "model": "mock-rule-based-v1",
                            "api_key_env": "MOCK_PLACEHOLDER",
                            "roles": ["critic"],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "analyze",
                str(tmp_path / "x.txt"),
                "--llm-profiles",
                str(path),
                "--quality",
                "--quality-mode",
                "maximum",
            ],
        )
        assert result.exit_code != 0

    def test_quality_requires_profiles(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from book2skill.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["analyze", str(tmp_path / "x.txt"), "--quality"],
        )
        assert result.exit_code != 0


class TestMockLLMReviewer:
    def test_mock_reviewer_via_runtime_adapter(self) -> None:
        from book2skill.llm.quality import LLMReviewer
        from book2skill.llm.runtime import LLMRuntimeConfig, RuntimeLLMAdapter

        adapter = RuntimeLLMAdapter(LLMRuntimeConfig(provider="mock"))
        reviewer = LLMReviewer(adapter, role="critic", model_id="mock-critic")
        unit = _unit("u1", confidence=0.1)
        card = build_evidence_card(unit, _bundle(unit))
        patch = reviewer.review(card, unit)
        assert patch.references_known(unit)
        assert patch.disposition == "merge"
