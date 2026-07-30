"""Tests for the optional SQLite storage backend (P2)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from book2skill.domain import (
    ExtractionMapEntry,
    Locator,
    LocatorKind,
    SourceFormat,
    SourceManifest,
)
from book2skill.storage.sqlite_storage import SqliteRawStorage, SqliteSchemaStorage


def _manifest(
    source_id: str = "abc123def456",
    version: int = 1,
    sha256: str = "a" * 64,
) -> SourceManifest:
    return SourceManifest(
        source_id=source_id,
        version=version,
        original_name="doc.pdf",
        content_sha256=sha256,
        format=SourceFormat.PDF,
        rights_confirmed=True,
        ingested_at=datetime.now(timezone.utc),
    )


def _entry(source_id: str = "abc123def456", seq: int = 1) -> ExtractionMapEntry:
    return ExtractionMapEntry(
        block_id=f"{source_id}-p{seq}",
        source_id=source_id,
        text_sha256="b" * 64,
        locator=Locator(kind=LocatorKind.PARAGRAPH, paragraph=seq),
        confidence=1.0,
    )


class TestSqliteRawStorage:
    def test_save_and_load_original(self, tmp_path: Path) -> None:
        store = SqliteRawStorage(tmp_path)
        data = b"hello world"
        store.save_original("abc123def456", 1, data, "doc.txt")
        assert store.load_original("abc123def456", 1) == data
        store.close()

    def test_load_original_missing_raises(self, tmp_path: Path) -> None:
        store = SqliteRawStorage(tmp_path)
        with pytest.raises(KeyError):
            store.load_original("missing", 1)
        store.close()

    def test_save_and_load_manifest(self, tmp_path: Path) -> None:
        store = SqliteRawStorage(tmp_path)
        m = _manifest()
        store.save_manifest(m)
        loaded = store.load_manifest(m.source_id, m.version)
        assert loaded.source_id == m.source_id
        assert loaded.content_sha256 == m.content_sha256
        assert loaded.format == m.format
        store.close()

    def test_load_manifest_missing_raises(self, tmp_path: Path) -> None:
        store = SqliteRawStorage(tmp_path)
        with pytest.raises(KeyError):
            store.load_manifest("missing", 1)
        store.close()

    def test_save_and_load_extraction_map(self, tmp_path: Path) -> None:
        store = SqliteRawStorage(tmp_path)
        entries = [_entry(seq=1), _entry(seq=2), _entry(seq=3)]
        store.save_extraction_map("abc123def456", 1, entries)
        loaded = store.load_extraction_map("abc123def456", 1)
        assert len(loaded) == 3
        assert loaded[0].block_id == entries[0].block_id
        assert loaded[2].block_id == entries[2].block_id
        store.close()

    def test_save_extraction_map_replaces(self, tmp_path: Path) -> None:
        store = SqliteRawStorage(tmp_path)
        store.save_extraction_map("abc123def456", 1, [_entry(seq=1)])
        store.save_extraction_map("abc123def456", 1, [_entry(seq=9)])
        loaded = store.load_extraction_map("abc123def456", 1)
        assert len(loaded) == 1
        assert loaded[0].block_id == _entry(seq=9).block_id
        store.close()

    def test_exists(self, tmp_path: Path) -> None:
        store = SqliteRawStorage(tmp_path)
        assert not store.exists("abc123def456", 1)
        store.save_manifest(_manifest())
        assert store.exists("abc123def456", 1)
        store.close()

    def test_list_versions(self, tmp_path: Path) -> None:
        store = SqliteRawStorage(tmp_path)
        store.save_manifest(_manifest(version=1))
        store.save_manifest(_manifest(version=3))
        store.save_manifest(_manifest(version=2))
        assert store.list_versions("abc123def456") == [1, 2, 3]
        store.close()

    def test_idempotent_save_original(self, tmp_path: Path) -> None:
        store = SqliteRawStorage(tmp_path)
        store.save_original("abc123def456", 1, b"v1", "doc.txt")
        store.save_original("abc123def456", 1, b"v2", "doc.txt")
        assert store.load_original("abc123def456", 1) == b"v2"
        store.close()

    def test_db_path_is_single_file(self, tmp_path: Path) -> None:
        store = SqliteRawStorage(tmp_path)
        assert store.db_path.name == "book2skill.db"
        assert store.db_path.exists()
        store.close()

    def test_requires_data_home(self) -> None:
        with pytest.raises(ValueError, match="data_home"):
            SqliteRawStorage(None)


class TestSqliteSchemaStorage:
    def test_save_and_load_collection(self, tmp_path: Path) -> None:
        store = SqliteSchemaStorage(tmp_path)
        data = {"name": "test", "count": 3}
        store.save_collection("col-1", data)
        loaded = store.load_collection("col-1")
        assert loaded == data
        store.close()

    def test_load_collection_missing_raises(self, tmp_path: Path) -> None:
        store = SqliteSchemaStorage(tmp_path)
        with pytest.raises(KeyError):
            store.load_collection("missing")
        store.close()

    def test_append_records_preserves_order(self, tmp_path: Path) -> None:
        store = SqliteSchemaStorage(tmp_path)
        store.append_records("col-1", [{"i": 0}, {"i": 1}])
        store.append_records("col-1", [{"i": 2}])
        # Verify by querying the DB directly (records are stored as JSONL-like rows).
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT record_json FROM records WHERE collection_id = ? ORDER BY seq",
            ("col-1",),
        ).fetchall()
        items = [json.loads(r[0]) for r in rows]
        assert [item["i"] for item in items] == [0, 1, 2]
        store.close()
