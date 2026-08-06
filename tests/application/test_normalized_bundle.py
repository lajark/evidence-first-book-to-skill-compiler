from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import validate
from typer.testing import CliRunner

from book2skill.application.models import AnalysisBundle, CandidateUnit
from book2skill.application.normalized_bundle import (
    load_normalized_bundle,
    normalize_analysis_bundle,
    verify_normalized_bundle,
    write_normalized_bundle,
)
from book2skill.cli import app
from book2skill.domain import ConflictRecord, ConflictStatus, DomainError, ReviewItem
from book2skill.resources import schema_file


def _bundle(
    *, source_id: str = "src-1", quote: str = "short evidence"
) -> AnalysisBundle:
    return AnalysisBundle(
        collection_id="collection-1",
        source_ids=["src-1"],
        structure=[],
        candidate_units=[
            CandidateUnit(
                unit_id="unit-1",
                kind="technique",
                content="Apply one bounded technique.",
                source_refs=[
                    {"source_id": source_id, "block_id": "block-1", "quote": quote}
                ],
                confidence=0.9,
                review_status="approved",
            )
        ],
        review_queue=[],
    )


def test_normalization_is_deterministic_and_sanitizes_quotes() -> None:
    first = normalize_analysis_bundle(_bundle())
    second = normalize_analysis_bundle(_bundle())

    assert first.bundle == second.bundle
    assert verify_normalized_bundle(first.bundle)
    ref = first.bundle.units[0].source_refs[0]
    assert ref.quote_chars == len("short evidence")
    assert ref.quote_sha256 is not None
    assert "short evidence" not in first.bundle.model_dump_json()


def test_normalized_bundle_conforms_to_public_schema() -> None:
    bundle = normalize_analysis_bundle(_bundle()).bundle
    schema = json.loads(
        schema_file("normalized-bundle.schema.json").read_text(encoding="utf-8")
    )
    validate(instance=bundle.model_dump(mode="json"), schema=schema)


def test_unknown_source_ref_fails_closed() -> None:
    with pytest.raises(DomainError, match="unknown source"):
        normalize_analysis_bundle(_bundle(source_id="invented"))


def test_written_bundle_detects_identity_tampering(tmp_path: Path) -> None:
    path = write_normalized_bundle(
        tmp_path, normalize_analysis_bundle(_bundle()).bundle
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["units"][0]["content"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="identity"):
        load_normalized_bundle(path)


def test_normalize_cli_writes_replayable_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "analysis.json"
    output = tmp_path / "snapshot.json"
    source.write_text(_bundle().model_dump_json(indent=2), encoding="utf-8")

    result = CliRunner().invoke(
        app, ["normalize", str(source), "--output", str(output), "--json"]
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["normalized_bundle_id"]
    assert load_normalized_bundle(output).collection_id == "collection-1"


def test_legacy_v1_bundle_defaults_new_evidence_fields() -> None:
    payload = _bundle().model_dump(mode="json")
    payload["candidate_units"][0].pop("evidence_level")
    payload["candidate_units"][0].pop("evidence_note")

    restored = AnalysisBundle.model_validate(payload)
    normalized = normalize_analysis_bundle(restored).bundle

    assert restored.candidate_units[0].evidence_level == "primary"
    assert str(normalized.units[0].evidence_level) == "primary"


def test_inferred_candidate_requires_an_evidence_note() -> None:
    with pytest.raises(ValueError, match="evidence_note"):
        CandidateUnit(
            unit_id="unit-inferred",
            kind="principle",
            content="An inference.",
            source_refs=[{"source_id": "src-1", "block_id": "block-1"}],
            confidence=0.5,
            review_status="candidate",
            evidence_level="inferred",
        )


def test_open_review_state_is_preserved_for_replay() -> None:
    bundle = _bundle().model_copy(
        update={
            "conflicts": [
                ConflictRecord(
                    conflict_id="conflict-1",
                    unit_ids=["unit-1", "unit-2"],
                    description="Two source views require review.",
                    status=ConflictStatus.OPEN,
                )
            ],
            "review_queue": [
                ReviewItem(
                    item_id="review-1",
                    ref_type="conflict",
                    ref_id="conflict-1",
                    reason="open_conflict",
                    severity="error",
                    disposition="open",
                )
            ],
        }
    )

    normalized = normalize_analysis_bundle(bundle).bundle

    assert str(normalized.conflicts[0].status) == "open"
    assert normalized.review_queue[0].disposition == "open"
