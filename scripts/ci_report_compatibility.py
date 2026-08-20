"""Publish compatibility report failures as compact GitHub annotations."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: ci_report_compatibility.py REPORT.json", file=sys.stderr)
        return 2

    report_path = Path(sys.argv[1])
    if not report_path.is_file():
        print(f"::warning::compatibility report not found: {report_path}")
        return 0

    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        "::notice title=compatibility summary::"
        f"status={report.get('status', 'unknown')}"
    )
    for result in report.get("external_results", []):
        if result.get("status") == "pass":
            continue
        tool_id = result.get("tool_id", "unknown")
        status = result.get("status", "unknown")
        exit_code = result.get("exit_code")
        message = " ".join(str(result.get("message", "")).split())
        evidence = ",".join(str(item) for item in result.get("evidence", []))
        print(
            "::error title=external validator::"
            f"{tool_id}: status={status}; exit_code={exit_code}; "
            f"evidence={evidence or 'none'}; {message}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
