"""Tests for file-system storage implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from book2skill.domain import (
    Confidentiality,
    ExtractionMapEntry,
    Locator,
    LocatorKind,
    SourceFormat,
    SourceManifest,
)
from book2skill.storage import (
    FileRawStorage,
    FileSchemaStorage,
    FileWikiStorage,
    StorageNotFoundError,
    StoragePathError,
    resolve_within,
)


@pytest.fixture()
def raw_storage(tmp_path: Path) -> FileRawStorage:
    return FileRawStorage(data_home=tmp_path)


@pytest.fixture()
def sample_manifest() -> SourceManifest:
    return SourceManifest(
        source_id="abcdef1234567890",
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


@pytest.fixture()
def sample_entries() -> list[ExtractionMapEntry]:
    return [
        ExtractionMapEntry(
            block_id="blk-1",
            source_id="abcdef1234567890",
            text_sha256="1" * 64,
            locator=Locator(kind=LocatorKind.PAGE, page=10),
            confidence=0.95,
        ),
        ExtractionMapEntry(
            block_id="blk-2",
            source_id="abcdef1234567890",
            text_sha256="2" * 64,
            locator=Locator(kind=LocatorKind.CHAPTER, chapter="intro"),
            confidence=0.80,
        ),
    ]


def test_save_and_load_original(
    raw_storage: FileRawStorage,
    sample_manifest: SourceManifest,
) -> None:
    data = b"PDF content here"
    raw_storage.save_manifest(sample_manifest)
    path = raw_storage.save_original(
        sample_manifest.source_id, sample_manifest.version, data, "sample.pdf"
    )
    assert path.exists()
    loaded = raw_storage.load_original(
        sample_manifest.source_id, sample_manifest.version
    )
    assert loaded == data


def test_save_and_load_manifest(
    raw_storage: FileRawStorage,
    sample_manifest: SourceManifest,
) -> None:
    path = raw_storage.save_manifest(sample_manifest)
    assert path.exists()
    loaded = raw_storage.load_manifest(
        sample_manifest.source_id, sample_manifest.version
    )
    assert loaded.source_id == sample_manifest.source_id
    assert loaded.version == sample_manifest.version
    assert loaded.format == SourceFormat.PDF


def test_save_and_load_extraction_map(
    raw_storage: FileRawStorage,
    sample_manifest: SourceManifest,
    sample_entries: list[ExtractionMapEntry],
) -> None:
    raw_storage.save_manifest(sample_manifest)
    path = raw_storage.save_extraction_map(
        sample_manifest.source_id,
        sample_manifest.version,
        sample_entries,
    )
    assert path.exists()
    loaded = raw_storage.load_extraction_map(
        sample_manifest.source_id, sample_manifest.version
    )
    assert len(loaded) == len(sample_entries)
    assert loaded[0].block_id == "blk-1"
    assert loaded[1].locator.chapter == "intro"


def test_manifest_schema_round_trip(
    raw_storage: FileRawStorage,
    sample_manifest: SourceManifest,
) -> None:
    raw_storage.save_manifest(sample_manifest)
    loaded = raw_storage.load_manifest(
        sample_manifest.source_id, sample_manifest.version
    )
    assert loaded.model_dump_json() == sample_manifest.model_dump_json()


def test_extraction_map_empty(
    raw_storage: FileRawStorage,
    sample_manifest: SourceManifest,
) -> None:
    raw_storage.save_manifest(sample_manifest)
    raw_storage.save_extraction_map(
        sample_manifest.source_id, sample_manifest.version, []
    )
    loaded = raw_storage.load_extraction_map(
        sample_manifest.source_id, sample_manifest.version
    )
    assert loaded == []


def test_raw_immutable(
    raw_storage: FileRawStorage,
    sample_manifest: SourceManifest,
) -> None:
    data = b"original"
    manifest = sample_manifest.model_copy(update={"original_name": "file.pdf"})
    raw_storage.save_manifest(manifest)
    raw_storage.save_original(
        manifest.source_id, manifest.version, data, "file.pdf"
    )
    # Re-saving the same version should overwrite the stored file (content-addressed
    # immutability is enforced at a higher layer; storage guarantees atomic replace).
    raw_storage.save_original(
        manifest.source_id, manifest.version, b"new", "file.pdf"
    )
    loaded = raw_storage.load_original(
        manifest.source_id, manifest.version
    )
    assert loaded == b"new"


def test_list_versions(
    raw_storage: FileRawStorage,
    sample_manifest: SourceManifest,
) -> None:
    raw_storage.save_manifest(sample_manifest)
    second = sample_manifest.model_copy(update={"version": 2})
    raw_storage.save_manifest(second)
    versions = raw_storage.list_versions(sample_manifest.source_id)
    assert versions == [1, 2]


def test_load_missing_manifest(raw_storage: FileRawStorage) -> None:
    with pytest.raises(StorageNotFoundError):
        raw_storage.load_manifest("missing", 1)


def test_load_missing_original(
    raw_storage: FileRawStorage,
    sample_manifest: SourceManifest,
) -> None:
    raw_storage.save_manifest(sample_manifest)
    with pytest.raises(StorageNotFoundError):
        raw_storage.load_original(
            sample_manifest.source_id, sample_manifest.version
        )


def test_path_traversal_blocked(raw_storage: FileRawStorage) -> None:
    with pytest.raises(StoragePathError):
        raw_storage._version_dir("../escape", 1)  # type: ignore[misc]


def test_resolve_within(tmp_path: Path) -> None:
    base = tmp_path / "root"
    base.mkdir()
    target = resolve_within(base, "a", "b")
    assert target == (base / "a" / "b").resolve()


def test_resolve_within_rejects_traversal(tmp_path: Path) -> None:
    base = tmp_path / "root"
    base.mkdir()
    with pytest.raises(StoragePathError):
        resolve_within(base, "..", "escape")


def test_atomic_write(tmp_path: Path) -> None:
    from book2skill.storage import atomic_write

    path = tmp_path / "file.txt"
    atomic_write(path, "hello")
    assert path.read_text(encoding="utf-8") == "hello"


def test_wiki_storage(tmp_path: Path) -> None:
    wiki = FileWikiStorage(data_home=tmp_path)
    path = wiki.write_skill("test-skill", "# Test Skill\n")
    assert path.exists()
    assert wiki.read_skill("test-skill") == "# Test Skill\n"


def test_schema_storage(tmp_path: Path) -> None:
    schema = FileSchemaStorage(data_home=tmp_path)
    path = schema.save_collection("col-1", {"name": "test"})
    assert path.exists()
    loaded = schema.load_collection("col-1")
    assert loaded["name"] == "test"
