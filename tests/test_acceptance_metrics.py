"""Regression tests for A-G acceptance quality and benchmark evidence."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.acceptance_metrics import (
    acceptance_failures,
    analyze_bundle_metrics,
    skill_tree_metrics,
)
from scripts.benchmark_analysis import run_case


def test_analysis_metrics_reject_unsourced_or_duplicate_candidates() -> None:
    metrics = analyze_bundle_metrics(
        {
            "source_ids": ["source-a"],
            "candidate_units": [
                {
                    "unit_id": "unit-a",
                    "source_refs": [{"source_id": "source-a", "block_id": "p1"}],
                },
                {"unit_id": "unit-a", "source_refs": []},
            ],
        }
    )

    assert metrics["source_reference_coverage"] == 0.5
    assert metrics["unsupported_candidate_count"] == 1
    assert metrics["duplicate_candidate_id_count"] == 1
    assert acceptance_failures(metrics)


def test_skill_metrics_require_declared_citations_and_passing_quality(
    tmp_path: Path,
) -> None:
    (tmp_path / "references").mkdir()
    (tmp_path / "provenance.yml").write_text(
        "sources:\n  - source_id: source-a\n", encoding="utf-8"
    )
    (tmp_path / "references" / "provenance.md").write_text(
        "- source-a / source-a-p1\n", encoding="utf-8"
    )
    (tmp_path / "quality-report.json").write_text(
        json.dumps(
            {
                "checks": [
                    {"check_id": "source-coverage", "status": "pass", "evidence": []}
                ]
            }
        ),
        encoding="utf-8",
    )

    metrics = skill_tree_metrics(tmp_path)

    assert metrics["citation_source_hit_rate"] == 1.0
    assert metrics["quality_fail_check_count"] == 0
    assert acceptance_failures(metrics) == []


def test_benchmark_case_records_required_pipeline_metrics() -> None:
    measurement = run_case(10)

    assert measurement["block_count"] == 10
    assert measurement["peak_rss_bytes"] > 0
    assert measurement["source_read_operations"]["logical_full_reads"] == 3
    assert measurement["llm"]["call_count"] >= 2
    # Chapter→book reduce caps the evidence set (mock: 8/chapter), so 10
    # source blocks collapse to 8 sourced candidates with full provenance.
    assert measurement["output_quality"]["candidate_count"] == 8
    assert measurement["output_quality"]["source_reference_coverage"] == 1.0
