"""Publish concise pytest failures as GitHub Actions annotations."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: ci_report_pytest.py REPORT.xml", file=sys.stderr)
        return 2

    report_path = Path(sys.argv[1])
    if not report_path.is_file():
        print(f"::warning::pytest report not found: {report_path}")
        return 0

    root = ET.parse(report_path).getroot()
    failures = [
        testcase
        for testcase in root.iter("testcase")
        if testcase.find("failure") is not None
        or testcase.find("error") is not None
    ]
    for testcase in failures:
        failure = testcase.find("failure")
        if failure is None:
            failure = testcase.find("error")
        if failure is None:
            continue
        classname = testcase.attrib.get("classname", "pytest")
        name = testcase.attrib.get("name", "unknown")
        detail = " ".join((failure.text or "").split())
        message = f"{classname}::{name}: {detail[:1000]}"
        print(f"::error title=pytest failure::{message}")
    print(f"::notice title=pytest summary::failed tests: {len(failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
