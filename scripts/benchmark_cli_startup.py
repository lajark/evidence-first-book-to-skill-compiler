"""Measure fresh-process CLI startup without importing pipeline commands.

The benchmark intentionally runs the public ``python -m book2skill.cli --help``
entry point in a new Python process for each sample. It records a baseline for
regression review, not a timing threshold: hosts and virtualised CI runners
have too much variance for a fixed cross-platform limit to be meaningful.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path


def _sample_startup() -> float:
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "book2skill.cli", "--help"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "CLI help command failed")
    return elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")

    measurements = [_sample_startup() for _ in range(args.samples)]
    payload = {
        "command": [sys.executable, "-m", "book2skill.cli", "--help"],
        "samples": args.samples,
        "seconds": {
            "min": round(min(measurements), 4),
            "median": round(statistics.median(measurements), 4),
            "max": round(max(measurements), 4),
        },
    }
    rendered = json.dumps(payload, indent=2) + "\n"
    if args.json_out is not None:
        args.json_out.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
