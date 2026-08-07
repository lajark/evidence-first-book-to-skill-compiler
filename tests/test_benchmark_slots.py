"""Tests for the public benchmark-slot detection contracts.

The internal A/B/C benchmark harness (scripts/benchmark_*.py) and its rubric
data live under .workspace/benchmark/ and are not shipped; only the public
benchmark-slot parsing, coverage and quality-gate integration are tested here,
using synthetic slots.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from book2skill.application.models import (
    AnalysisBundle,
    CandidateUnit,
    StructureEntry,
)
from book2skill.llm.benchmark_slots import (
    BenchmarkSlot,
    compute_slot_coverage,
    load_benchmark_slots,
    missing_slots,
)
from book2skill.llm.quality import QualityGate
from book2skill.llm.runtime import AnalysisRunManifest


def _unit(unit_id: str, *, kind: str = "principle", content: str) -> CandidateUnit:
    return CandidateUnit(
        unit_id=unit_id,
        kind=kind,
        content=content,
        conditions=[],
        exceptions=[],
        source_refs=[{"source_id": "s1", "block_id": "b1", "quote": content[:20]}],
        confidence=0.8,
        review_status="candidate",
    )


def _bundle(*units: CandidateUnit) -> AnalysisBundle:
    return AnalysisBundle(
        collection_id="c1",
        source_ids=["s1"],
        structure=[
            StructureEntry(
                block_id="b1", locator={}, heading="H", level=1, text_preview="H"
            )
        ],
        candidate_units=list(units),
        review_queue=[],
        conflicts=[],
        analysis_run=AnalysisRunManifest(
            provider="mock",
            model="mock-rule-based-v1",
            prompt_version="analysis-v4",
            response_schema_version="analysis-response-v2",
            parameters={},
            fallback_allowed=False,
        ),
    )


class TestBenchmarkSlots:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            load_benchmark_slots(tmp_path / "nope.yaml")

    def test_bad_schema_version_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("schema_version: 99\nbook_id: x\nslots: []\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_benchmark_slots(path)

    def test_compute_coverage_matches_by_kind_and_keyword(self) -> None:
        slots = [
            BenchmarkSlot(
                slot_id="A1",
                rubric_item_id="A1",
                kinds=["principle"],
                keywords=["谋攻"],
                min_count=1,
            )
        ]
        bundle = _bundle(
            _unit("u1", content="谋攻为上，不战而屈人之兵。"),
            _unit("u2", content="A different principle without the keyword."),
        )
        coverage = compute_slot_coverage(bundle.candidate_units, slots)
        assert coverage["A1"] == 1
        assert missing_slots(coverage, slots) == []

    def test_unsourced_candidate_does_not_cover(self) -> None:
        slots = [
            BenchmarkSlot(
                slot_id="A1",
                rubric_item_id="A1",
                kinds=["principle"],
                keywords=["谋攻"],
                min_count=1,
            )
        ]
        unit = _unit("u1", content="上兵伐谋。")
        unit.source_refs = []
        coverage = compute_slot_coverage([unit], slots)
        assert coverage["A1"] == 0
        assert missing_slots(coverage, slots) == ["A1"]

    def test_min_count_gap_reported(self) -> None:
        slots = [
            BenchmarkSlot(
                slot_id="C1",
                rubric_item_id="C1",
                kinds=["principle"],
                keywords=["step"],
                min_count=2,
            )
        ]
        bundle = _bundle(_unit("u1", content="step one only."))
        coverage = compute_slot_coverage(bundle.candidate_units, slots)
        assert coverage["C1"] == 1
        assert missing_slots(coverage, slots) == ["C1"]

    def test_structure_keywords_count_matching_headings(self) -> None:
        slots = [
            BenchmarkSlot(
                slot_id="A1",
                rubric_item_id="A1",
                kinds=["principle"],
                keywords=["chapter"],
                min_count=2,
                structure_keywords=["始计", "作战篇"],
            )
        ]
        structure = [
            StructureEntry(
                block_id="b1",
                locator={},
                heading="第一篇 始计",
                level=1,
                text_preview="",
            ),
            {
                "heading": "第二篇 作战篇",
                "text_preview": "",
            },
            {
                "heading": "无关章节",
                "text_preview": "",
            },
        ]
        coverage = compute_slot_coverage([], slots, structure=structure)
        assert coverage["A1"] == 2
        assert missing_slots(coverage, slots) == []

    def test_structure_evidence_does_not_change_slots_without_opt_in(self) -> None:
        slots = [
            BenchmarkSlot(
                slot_id="A1",
                rubric_item_id="A1",
                kinds=["principle"],
                keywords=["chapter"],
                min_count=1,
            )
        ]
        structure = [
            StructureEntry(
                block_id="b1",
                locator={},
                heading="chapter heading",
                level=1,
                text_preview="",
            )
        ]
        coverage = compute_slot_coverage([], slots, structure=structure)
        assert coverage["A1"] == 0

    def test_structure_coverage_is_used_by_quality_gate(self) -> None:
        slots = [
            BenchmarkSlot(
                slot_id="A1",
                rubric_item_id="A1",
                kinds=["principle"],
                keywords=["chapter"],
                min_count=1,
                structure_keywords=["始计"],
            )
        ]
        bundle = _bundle()
        bundle.structure[0].heading = "第一篇 始计"
        flags = QualityGate.detect(bundle, benchmark_slots=slots)
        assert not any(f.reason == "benchmark_gap" for f in flags)


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
        bundle = _bundle(_unit("u1", content="unrelated content without keywords."))
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
        bundle = _bundle(_unit("u1", content="上兵伐谋，谋攻为上。"))
        flags = QualityGate.detect(bundle, benchmark_slots=slots)
        assert not any(f.reason == "benchmark_gap" for f in flags)

    def test_no_slots_means_no_benchmark_flags(self) -> None:
        bundle = _bundle(_unit("u1", content="a normal candidate."))
        flags = QualityGate.detect(bundle)
        assert not any(f.reason == "benchmark_gap" for f in flags)
