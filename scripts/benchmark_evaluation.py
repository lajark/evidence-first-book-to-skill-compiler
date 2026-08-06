"""Validate external rubric evaluations before benchmark-score backfill.

The A/B/C harness deliberately does not infer a semantic quality score from
slot coverage.  This module validates the separate human/evaluator artifact
that is allowed to populate ``benchmark_score`` in a measurement report.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

EXPECTED_BENCHMARK_IDS: dict[str, str] = {
    "sunzi": "book2skill-sunzi-zh-v1",
    "pomodoro": "book2skill-pomodoro-illustrated-zh-v1",
    "mini_habits": "book2skill-mini-habits-zh-v1",
}
EVALUATION_CONFIDENCES = {"high", "medium", "low"}


def load_evaluations(path: Path) -> dict[tuple[str, str], dict[str, object]]:
    """Load and validate evaluator records from a JSON file or directory.

    A file may contain either the rubric's one-record object or an envelope
    with an ``evaluations`` array.  A directory loads all ``*.json`` files.
    Only the audit fields needed for a measurement are copied; rubric detail
    remains in the evaluator artifact and is never merged into a benchmark
    report.
    """
    paths = sorted(path.glob("*.json")) if path.is_dir() else [path]
    if not paths:
        raise ValueError(f"no evaluator JSON files found: {path}")

    records: list[dict[str, Any]] = []
    for record_path in paths:
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid evaluator JSON: {record_path}") from exc
        if isinstance(payload, dict) and isinstance(payload.get("evaluations"), list):
            entries = payload["evaluations"]
        else:
            entries = [payload]
        if any(not isinstance(entry, dict) for entry in entries):
            raise ValueError(f"evaluator records must be JSON objects: {record_path}")
        records.extend(entries)

    result: dict[tuple[str, str], dict[str, object]] = {}
    for index, raw in enumerate(records, start=1):
        record = _validate_record(raw, index)
        key = (str(record["book_id"]), str(record["strategy"]))
        if key in result:
            raise ValueError(
                f"duplicate evaluator record for {key[0]}/{key[1]}"
            )
        result[key] = {
            field: record[field]
            for field in (
                "benchmark_id",
                "benchmark_version",
                "evaluator",
                "evaluation_date",
                "confidence",
                "final_score",
            )
        }
    return result


def attach_evaluation(
    measurement: dict[str, Any], evaluation: dict[str, object] | None
) -> dict[str, Any]:
    """Attach one validated score to a successful measurement only."""
    if evaluation is None:
        return measurement
    if measurement.get("source_error"):
        raise ValueError(
            "an evaluator score cannot attach to a failed/source-error measurement"
        )
    measurement["benchmark_score"] = evaluation["final_score"]
    measurement["benchmark_evaluation"] = evaluation
    return measurement


def _validate_record(raw: dict[str, Any], index: int) -> dict[str, Any]:
    def required_text(field: str) -> str:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"evaluation {index}: {field} must be non-empty text")
        return value.strip()

    book_id = required_text("book_id")
    strategy = required_text("strategy")
    expected_id = EXPECTED_BENCHMARK_IDS.get(book_id)
    if expected_id is None:
        raise ValueError(f"evaluation {index}: unsupported book_id {book_id!r}")
    if strategy not in {"single", "balanced", "quality"}:
        raise ValueError(f"evaluation {index}: unsupported strategy {strategy!r}")

    benchmark_id = required_text("benchmark_id")
    if benchmark_id != expected_id:
        raise ValueError(
            f"evaluation {index}: benchmark_id {benchmark_id!r} does not match "
            f"book_id {book_id!r}"
        )
    benchmark_version = required_text("benchmark_version")
    if benchmark_version != "1.0":
        raise ValueError(
            f"evaluation {index}: unsupported benchmark_version {benchmark_version!r}"
        )
    evaluator = required_text("evaluator")
    evaluation_date = required_text("evaluation_date")
    confidence = required_text("confidence").lower()
    if confidence not in EVALUATION_CONFIDENCES:
        raise ValueError(f"evaluation {index}: invalid confidence {confidence!r}")

    score = raw.get("final_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError(f"evaluation {index}: final_score must be numeric")
    if not math.isfinite(float(score)) or not 0 <= score <= 100:
        raise ValueError(f"evaluation {index}: final_score must be between 0 and 100")

    return {
        "book_id": book_id,
        "strategy": strategy,
        "benchmark_id": benchmark_id,
        "benchmark_version": benchmark_version,
        "evaluator": evaluator,
        "evaluation_date": evaluation_date,
        "confidence": confidence,
        "final_score": round(float(score), 1),
    }


__all__ = ["EXPECTED_BENCHMARK_IDS", "attach_evaluation", "load_evaluations"]
