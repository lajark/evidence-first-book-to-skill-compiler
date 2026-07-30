"""Tests for domain models."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import validate
from jsonschema.validators import Draft202012Validator
from pydantic import ValidationError

from book2skill.domain import (
    Confidentiality,
    ExtractionMapEntry,
    Locator,
    LocatorKind,
    SourceFormat,
    SourceManifest,
)


def _load_schema(name: str) -> dict:
    return json.loads(Path("schemas", name).read_text(encoding="utf-8"))


def test_source_manifest_round_trip_and_schema() -> None:
    manifest = SourceManifest(
        source_id="abc12345",
        version=1,
        original_name="sample.pdf",
        content_sha256="0" * 64,
        format=SourceFormat.PDF,
        rights_confirmed=True,
        rights_note="Own copy",
        confidentiality=Confidentiality.PERSONAL,
        extractor="pdf_adapter",
        extractor_version="0.1.0",
        ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    data = json.loads(manifest.model_dump_json())
    schema = _load_schema("source-manifest.schema.json")
    validate(instance=data, schema=schema, cls=Draft202012Validator)
    assert data["format"] == "pdf"
    assert data["rights_confirmed"] is True


def test_source_manifest_rejects_invalid_sha() -> None:
    with pytest.raises(ValidationError):
        SourceManifest(
            source_id="abc12345",
            version=1,
            content_sha256="not-a-sha",
            format=SourceFormat.TXT,
            rights_confirmed=True,
            ingested_at=datetime.now(timezone.utc),
        )


def test_source_manifest_rejects_false_rights_confirmed() -> None:
    with pytest.raises(ValidationError):
        SourceManifest(
            source_id="abc12345",
            version=1,
            content_sha256="0" * 64,
            format=SourceFormat.TXT,
            rights_confirmed=False,  # type: ignore[typeddict-item]
            ingested_at=datetime.now(timezone.utc),
        )


def test_source_manifest_naive_datetime_gets_utc() -> None:
    manifest = SourceManifest(
        source_id="abc12345",
        version=1,
        content_sha256="0" * 64,
        format=SourceFormat.MD,
        rights_confirmed=True,
        ingested_at=datetime(2026, 1, 1),
    )
    assert manifest.ingested_at.tzinfo == timezone.utc


def test_extraction_map_entry_round_trip_and_schema() -> None:
    entry = ExtractionMapEntry(
        block_id="blk-1",
        source_id="src-1",
        text_sha256="1" * 64,
        locator=Locator(kind=LocatorKind.PAGE, page=10),
        confidence=0.95,
    )
    data = json.loads(entry.model_dump_json())
    schema = _load_schema("extraction-map-entry.schema.json")
    validate(instance=data, schema=schema, cls=Draft202012Validator)
    assert data["locator"]["page"] == 10


def test_extraction_map_entry_all_locator_kinds() -> None:
    for kind in LocatorKind:
        entry = ExtractionMapEntry(
            block_id=f"blk-{kind.value}",
            source_id="src-1",
            text_sha256="2" * 64,
            locator=Locator(kind=kind),
        )
        assert entry.locator.kind == kind


def test_locator_rejects_non_positive_page() -> None:
    with pytest.raises(ValidationError):
        Locator(kind=LocatorKind.PAGE, page=0)


def test_extraction_map_entry_rejects_invalid_confidence() -> None:
    with pytest.raises(ValidationError):
        ExtractionMapEntry(
            block_id="blk-1",
            source_id="src-1",
            text_sha256="3" * 64,
            locator=Locator(kind=LocatorKind.PARAGRAPH, paragraph=1),
            confidence=1.5,
        )
