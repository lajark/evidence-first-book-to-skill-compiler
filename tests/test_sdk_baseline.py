"""Regression guards for the public SDK and JSON Schema contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import book2skill.sdk as sdk
from book2skill.resources import schema_file

_FIXTURES = Path(__file__).parent / "fixtures"


def test_sdk_public_surface_matches_versioned_baseline() -> None:
    baseline = json.loads(
        (_FIXTURES / "sdk_api_baseline.json").read_text(encoding="utf-8")
    )

    assert baseline["sdk_version"] == sdk.SDK_VERSION
    assert sorted(sdk.__all__) == baseline["public_names"]
    assert all(hasattr(sdk, name) for name in sdk.__all__)


def test_versioned_schema_hashes_match_baseline() -> None:
    baseline: dict[str, str] = json.loads(
        (_FIXTURES / "schema_baseline.json").read_text(encoding="utf-8")
    )

    actual = {
        name: hashlib.sha256(schema_file(name).read_bytes()).hexdigest()
        for name in baseline
    }
    assert actual == baseline
