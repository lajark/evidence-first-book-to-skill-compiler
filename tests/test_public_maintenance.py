"""Regression tests for the public demo and maintainer-facing benchmark tools."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from scripts.run_acceptance import AcceptanceRunner, _parse_args

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_acceptance_defaults_to_explicit_mock_mode(tmp_path: Path) -> None:
    args = _parse_args([])
    assert args.llm_mode == "mock"
    runner = AcceptanceRunner(tmp_path / "run")
    assert runner._llm_args() == ["--llm", "mock"]


def test_acceptance_help_is_non_destructive() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_acceptance.py", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0
    assert "--output-dir" in result.stdout
    assert "--llm-mode" in result.stdout


def test_public_demo_builds_with_authoritative_integrity(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_public_demo.py",
            "--output-dir",
            str(output_dir),
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["integrity"] == {
        "artifact_ok": True,
        "content_report_match": True,
        "authoritative_source_check": True,
        "blocked": False,
    }
    assert (output_dir / "skill" / "SKILL.md").is_file()
    assert (output_dir / "skill" / "provenance.yml").is_file()


def test_public_benchmark_report_matches_schema(tmp_path: Path) -> None:
    output_dir = tmp_path / "benchmark"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_public_benchmark.py",
            "--output-dir",
            str(output_dir),
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report_path = output_dir / "benchmark-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (REPO_ROOT / "schemas" / "benchmark-report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            report
        )
    )
    assert errors == []
    assert report["benchmark_id"] == "public-demo-v1"
    assert report["metrics"]
