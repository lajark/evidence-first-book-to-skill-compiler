"""Tests for the A/B/C benchmark harness and benchmark-slot detection (OPT-P1-11)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.benchmark_abc import (
    BookSpec,
    _mock_profile_set,
    run_strategy,
)

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

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SLOTS_DIR = _REPO_ROOT / "benchmark_abc" / "slots"


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
    def test_load_real_slot_files(self) -> None:
        for name in ("sunzi.yaml", "pomodoro.yaml", "mini_habits.yaml"):
            slots = load_benchmark_slots(_SLOTS_DIR / name)
            assert slots, f"{name} must define slots"
            assert all(s.slot_id and s.rubric_item_id for s in slots)

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


def _synthetic_source(tmp_path: Path) -> Path:
    path = tmp_path / "book.txt"
    path.write_text(
        "\n\n".join(
            f"Principle: keep a durable constraint before committing capital. "
            f"Record {i} has a distinct evidence marker {i * 7919}."
            for i in range(1, 6)
        ),
        encoding="utf-8",
    )
    return path


def _synthetic_spec(tmp_path: Path) -> BookSpec:
    slots = [
        BenchmarkSlot(
            slot_id="A1",
            rubric_item_id="A1",
            kinds=["principle"],
            keywords=["durable constraint"],
            min_count=1,
        )
    ]
    return BookSpec(
        book_id="synthetic", source_path=_synthetic_source(tmp_path), slots=slots
    )


class TestBenchmarkAbcHarness:
    @pytest.mark.parametrize("strategy", ["single", "balanced", "quality"])
    def test_runs_strategy_mock(self, tmp_path: Path, strategy: str) -> None:
        spec = _synthetic_spec(tmp_path)
        profile_set = _mock_profile_set()
        measurement = run_strategy(
            spec, strategy, profile_set, data_home=tmp_path / "data"
        )
        assert measurement["book_id"] == "synthetic"
        assert measurement["strategy"] == strategy
        assert measurement["schema_version"] == 1
        assert measurement["wall_time_s"] >= 0
        assert measurement["block_count"] == 5
        assert measurement["output_quality"]["source_reference_coverage"] == 1.0
        assert measurement["llm"]["call_count"] >= 2
        assert measurement["benchmark_score"] is None
        assert "source_error" not in measurement or measurement["source_error"] is None

    def test_redaction_no_credentials(self, tmp_path: Path) -> None:
        spec = _synthetic_spec(tmp_path)
        measurement = run_strategy(
            spec, "single", _mock_profile_set(), data_home=tmp_path / "data"
        )
        dumped = json.dumps(measurement, ensure_ascii=False)
        assert "sk-" not in dumped
        assert "api_key" not in dumped
        assert "MOCK_PLACEHOLDER" not in dumped

class TestBenchmarkAbcReport:
    def test_report_renders_redacted(self, tmp_path: Path) -> None:
        from scripts.benchmark_abc import _render_measurement

        spec = _synthetic_spec(tmp_path)
        measurement = run_strategy(
            spec, "single", _mock_profile_set(), data_home=tmp_path / "data"
        )
        text = _render_measurement(measurement)
        assert "sk-" not in text
        assert "MOCK_PLACEHOLDER" not in text
        assert "durable constraint" not in text  # source text not echoed
