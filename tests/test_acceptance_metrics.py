"""Regression tests for A-G acceptance quality and benchmark evidence."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.acceptance_metrics import (
    acceptance_failures,
    analyze_bundle_metrics,
    skill_tree_metrics,
)


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


def test_skill_metrics_fail_on_content_integrity_report(tmp_path: Path) -> None:
    (tmp_path / "provenance.yml").write_text("sources: []\n", encoding="utf-8")
    (tmp_path / "quality-report.json").write_text(
        json.dumps({"checks": []}), encoding="utf-8"
    )
    (tmp_path / "content-integrity.json").write_text(
        json.dumps(
            {
                "blocked": True,
                "missing_unit_ids": ["unit-missing"],
                "content_mismatch_unit_ids": [],
            }
        ),
        encoding="utf-8",
    )

    metrics = skill_tree_metrics(tmp_path)

    assert metrics["content_integrity_blocked"] == 1
    assert metrics["content_integrity_missing_unit_count"] == 1
    assert acceptance_failures(metrics) == [
        "generated Skill content-integrity gate is blocked"
    ]
