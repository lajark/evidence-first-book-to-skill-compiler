"""Run the public deterministic demo benchmark and render one JSON source of truth."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = REPO_ROOT / "scripts" / "build_public_demo.py"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline public demo twice and write Benchmark v1."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".workspace/tmp/public-benchmark"),
        help="Exact empty directory for both runs and benchmark reports.",
    )
    parser.add_argument("--json", action="store_true", help="Print the report JSON.")
    return parser


def _run_demo(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(DEMO_SCRIPT), "--output-dir", str(path), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "demo failed")
    return json.loads(proc.stdout)


def _consumer_digest(skill_dir: Path) -> str:
    hasher = hashlib.sha256()
    roots = ["SKILL.md", "references", "assets", "scripts"]
    files: list[Path] = []
    for root in roots:
        path = skill_dir / root
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(p for p in path.rglob("*") if p.is_file())
    for path in sorted(files):
        relative = path.relative_to(skill_dir).as_posix().encode("utf-8")
        hasher.update(len(relative).to_bytes(4, "big"))
        hasher.update(relative)
        data = path.read_bytes()
        hasher.update(len(data).to_bytes(8, "big"))
        hasher.update(data)
    return hasher.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _metric(
    metric_id: str,
    status: str,
    value: float | int | None,
    unit: str,
    method: str,
    evidence: list[str],
    limitations: list[str],
) -> dict[str, Any]:
    return {
        "metric_id": metric_id,
        "status": status,
        "value": value,
        "unit": unit,
        "method": method,
        "evidence": evidence,
        "limitations": limitations,
    }


def _build_report(output_dir: Path, run1: Path, run2: Path) -> dict[str, Any]:
    skill = run1 / "skill"
    normalized = _load_json(skill / "normalized-bundle.json")
    integrity = _load_json(skill / "content-integrity.json")
    units = normalized.get("units", [])
    units_with_refs = [u for u in units if isinstance(u, dict) and u.get("source_refs")]
    source_refs = [
        ref
        for unit in units_with_refs
        for ref in unit.get("source_refs", [])
        if isinstance(ref, dict)
    ]
    block_ids = {
        ref.get("block_id") for ref in source_refs if ref.get("block_id")
    }
    extracted_blocks: set[str] = set()
    for path in (run1 / "workspace").glob("raw/*/1/extraction-map.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("block_id"):
                    extracted_blocks.add(record["block_id"])
    metrics = [
        _metric(
            "source_reference_coverage",
            "measured",
            len(units_with_refs) / len(units) if units else 1.0,
            "ratio",
            "Normalized units with one or more source_refs divided by all units.",
            ["skill/normalized-bundle.json"],
            ["This is provenance coverage, not semantic correctness."],
        ),
        _metric(
            "locator_resolution_rate",
            "measured",
            len(block_ids & extracted_blocks) / len(block_ids) if block_ids else 1.0,
            "ratio",
            "Referenced block IDs found in the immutable extraction map.",
            ["workspace/raw/*/1/extraction-map.jsonl"],
            ["Only structural locator resolution is measured."],
        ),
        _metric(
            "content_integrity_blocked",
            "measured",
            1 if integrity.get("blocked") else 0,
            "boolean-as-integer",
            "Persisted content-integrity report gate.",
            ["skill/content-integrity.json", "skill/compilation-artifact.json"],
            [],
        ),
        _metric(
            "duplicate_unit_id_count",
            "measured",
            len(units) - len({u.get("unit_id") for u in units if isinstance(u, dict)}),
            "count",
            "Duplicate unit IDs in the normalized bundle.",
            ["skill/normalized-bundle.json"],
            [],
        ),
        _metric(
            "rebuild_consumer_hash_equal",
            "measured",
            1
            if _consumer_digest(run1 / "skill") == _consumer_digest(run2 / "skill")
            else 0,
            "boolean-as-integer",
            "SHA-256 over SKILL.md, references, assets, and scripts from two runs.",
            ["run-1/skill", "run-2/skill"],
            ["Diagnostic files contain timestamps and are excluded."],
        ),
        _metric(
            "skill_fixture_pass_rate",
            "not_measured",
            None,
            "ratio",
            "Requires a host execution harness; this offline benchmark only checks "
            "fixture presence.",
            ["skill/skill-fixtures.json"],
            ["No Agent host is started by this benchmark."],
        ),
        _metric(
            "update_consistency",
            "not_measured",
            None,
            "ratio",
            "Requires an explicit second source version and Pack update replay.",
            [],
            ["The public demo has one immutable source version."],
        ),
        _metric(
            "semantic_hallucination_rate",
            "not_measured",
            None,
            "ratio",
            "Requires an independent human-labeled semantic evaluation set.",
            [],
            ["Provenance coverage must not be presented as a hallucination score."],
        ),
    ]
    return {
        "schema_version": "1.0",
        "benchmark_id": "public-demo-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "provider": "mock-rule-based-v1",
            "network": "disabled-by-contract",
        },
        "source_policy": "repository-authored-original",
        "cases": [
            {
                "case_id": "evidence-first-demo",
                "input_kind": "original-markdown",
                "rights_note": "Repository-authored demo text; no third-party content",
                "expected_outcome": "verified traceable Skill",
                "result": "passed" if not integrity.get("blocked") else "failed",
            }
        ],
        "metrics": metrics,
        "limitations": [
            "This is an offline structural benchmark, not a model-quality leaderboard.",
            "Human semantic metrics remain not_measured until an independent rubric "
            "is published.",
            "Runtime host execution and source updates require separate fixtures.",
        ],
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Public Evidence Benchmark v1",
        "",
        f"- Benchmark: `{report['benchmark_id']}`",
        f"- Generated: `{report['generated_at']}`",
        f"- Source policy: `{report['source_policy']}`",
        "",
        "The JSON report is the source of truth; this page is generated from it.",
        "",
        "## Metrics",
        "",
        "| Metric | Status | Value | Unit |",
        "|---|---|---:|---|",
    ]
    for metric in report["metrics"]:
        value = "—" if metric["value"] is None else str(metric["value"])
        lines.append(
            f"| `{metric['metric_id']}` | `{metric['status']}` | {value} | "
            f"`{metric['unit']}` |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            "python scripts/run_public_benchmark.py "
            "--output-dir .workspace/tmp/public-benchmark --json",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def run_benchmark(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(
            "Refusing to overwrite non-empty output directory: "
            f"{output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    run1 = output_dir / "run-1"
    run2 = output_dir / "run-2"
    _run_demo(run1)
    _run_demo(run2)
    report = _build_report(output_dir, run1, run2)
    (output_dir / "benchmark-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "benchmark-report.md").write_text(
        _render_markdown(report), encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_benchmark(args.output_dir)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"public benchmark failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        report_path = (
            Path(args.output_dir).expanduser().resolve() / "benchmark-report.json"
        )
        print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
