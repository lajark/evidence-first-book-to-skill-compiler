"""Regression coverage for the cold-start benchmark interface."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_cli_startup_benchmark_emits_machine_readable_summary(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    report_path = tmp_path / "startup.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_cli_startup.py",
            "--samples",
            "1",
            "--json-out",
            str(report_path),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["samples"] == 1
    assert payload["seconds"]["min"] >= 0
    assert json.loads(report_path.read_text(encoding="utf-8")) == payload
