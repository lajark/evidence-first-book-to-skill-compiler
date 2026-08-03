"""Direct tests for fail-closed KnowledgeUnit-to-Raw trace validation."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from book2skill.application.bundle_trace import verify_units_against_raw
from book2skill.domain import (
    DomainError,
    ErrorCode,
    ExtractionMapEntry,
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    Locator,
    LocatorKind,
    SourceFormat,
    SourceManifest,
    UnitKind,
)
from book2skill.storage import FileRawStorage


def _unit(
    *,
    unit_id: str = "unit-1",
    source_id: str = "source123",
    block_id: str = "source123-p1",
) -> KnowledgeUnit:
    return KnowledgeUnit(
        unit_id=unit_id,
        kind=UnitKind.PRINCIPLE,
        content="Validate every externally supplied identifier.",
        source_refs=[KnowledgeRef(source_id=source_id, block_id=block_id)],
        confidence=0.9,
        review_status=KnowledgeStatus.APPROVED,
    )


def _seed_raw(
    data_home: Path,
    *,
    source_id: str = "source123",
    block_ids: tuple[str, ...] = ("source123-p1",),
    entry_source_id: str | None = None,
) -> FileRawStorage:
    source = data_home.parent / f"{source_id}.txt"
    source.write_text(f"Trusted content for {source_id}.\n", encoding="utf-8")
    manifest = SourceManifest(
        source_id=source_id,
        version=1,
        original_name=source.name,
        content_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        format=SourceFormat.TXT,
        rights_confirmed=True,
        ingested_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        extractor="TextExtractor",
        extractor_version="1.0.0",
    )
    entries = [
        ExtractionMapEntry(
            block_id=block_id,
            source_id=entry_source_id or source_id,
            text_sha256=hashlib.sha256(block_id.encode()).hexdigest(),
            locator=Locator(
                kind=LocatorKind.PARAGRAPH,
                paragraph=index,
            ),
            confidence=1.0,
        )
        for index, block_id in enumerate(block_ids, start=1)
    ]
    storage = FileRawStorage(data_home)
    storage.save_ingest_from_path(manifest, source, entries)
    return storage


def test_verify_units_returns_verified_manifests(tmp_path: Path) -> None:
    storage = _seed_raw(tmp_path / "data")

    manifests = verify_units_against_raw(
        [_unit()],
        storage,
        source_version=1,
        input_id="collection:test",
    )

    assert [(item.source_id, item.version) for item in manifests] == [
        ("source123", 1)
    ]


def test_verify_units_without_raw_storage_fails_closed() -> None:
    with pytest.raises(DomainError) as exc:
        verify_units_against_raw(
            [_unit()],
            None,
            source_version=1,
            input_id="collection:test",
        )

    assert exc.value.code == ErrorCode.BUILD_SOURCE_TRACE_INVALID
    assert exc.value.details == {"reason": "raw_storage_unavailable"}
    assert "data-home" in exc.value.recovery


@pytest.mark.parametrize(
    ("source_id", "block_id", "invalid_field"),
    [
        ("", "source123-p1", "source_id"),
        ("source123", "", "block_id"),
    ],
)
def test_verify_units_rejects_empty_reference_fields(
    tmp_path: Path,
    source_id: str,
    block_id: str,
    invalid_field: str,
) -> None:
    with pytest.raises(DomainError) as exc:
        verify_units_against_raw(
            [_unit(source_id=source_id, block_id=block_id)],
            FileRawStorage(tmp_path / "data"),
            source_version=1,
            input_id="collection:test",
        )

    assert exc.value.code == ErrorCode.BUILD_SOURCE_TRACE_INVALID
    assert f"invalid {invalid_field}" in exc.value.message
    assert exc.value.details["unit_id"] == "unit-1"


def test_verify_units_requires_complete_raw_version(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as exc:
        verify_units_against_raw(
            [_unit()],
            FileRawStorage(tmp_path / "empty-data"),
            source_version=1,
            input_id="collection:test",
        )

    assert exc.value.details == {
        "reason": "raw_version_missing",
        "source_id": "source123",
        "version": 1,
    }


def test_verify_units_requires_block_in_matching_map(tmp_path: Path) -> None:
    storage = _seed_raw(tmp_path / "data", block_ids=("source123-p2",))

    with pytest.raises(DomainError) as exc:
        verify_units_against_raw(
            [_unit()],
            storage,
            source_version=1,
            input_id="collection:test",
        )

    assert "KnowledgeUnit unit-1 block_id source123-p1" in exc.value.message
    assert exc.value.details["reason"] == "raw_block_missing"


def test_verify_units_rejects_map_source_mismatch(tmp_path: Path) -> None:
    storage = _seed_raw(
        tmp_path / "data",
        entry_source_id="other-source",
    )

    with pytest.raises(DomainError) as exc:
        verify_units_against_raw(
            [_unit()],
            storage,
            source_version=1,
            input_id="collection:test",
        )

    assert "belongs to source other-source" in exc.value.message
    assert exc.value.details["reason"] == "block_source_mismatch"


def test_verify_units_deduplicates_sources_in_first_seen_order(
    tmp_path: Path,
) -> None:
    first = _seed_raw(
        tmp_path / "data",
        source_id="source123",
        block_ids=("source123-p1", "source123-p2"),
    )
    _seed_raw(
        tmp_path / "data",
        source_id="source456",
        block_ids=("source456-p1",),
    )
    units = [
        _unit(unit_id="unit-1", source_id="source456", block_id="source456-p1"),
        _unit(unit_id="unit-2", block_id="source123-p1"),
        _unit(unit_id="unit-3", block_id="source123-p2"),
    ]

    manifests = verify_units_against_raw(
        units,
        first,
        source_version=1,
        input_id="collection:test",
    )

    assert [item.source_id for item in manifests] == ["source456", "source123"]
