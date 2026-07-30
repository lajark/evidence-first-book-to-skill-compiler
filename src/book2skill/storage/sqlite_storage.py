"""Optional SQLite storage backend for RawStorage (P2).

Provides an alternative to :class:`~book2skill.storage.file_storage.FileRawStorage`
that stores raw originals, manifests and extraction maps in a single SQLite
database file. SQLite is part of the Python standard library, so this adds
**no new dependencies**.

The database file lives at ``<data_home>/book2skill.db``. The schema is
created lazily on first use. All data is versioned per source_id + version,
mirroring the file-based backend's append-only semantics.

This backend is opt-in: the default remains :class:`FileRawStorage` so the
project stays file-first and human-auditable via plain ``cat``/``grep``.
Use SQLite when you need indexed lookups across many sources or prefer a
single-file store.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from book2skill.domain import ExtractionMapEntry, SourceManifest

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS originals (
    source_id  TEXT NOT NULL,
    version    INTEGER NOT NULL,
    data       BLOB NOT NULL,
    original_name TEXT NOT NULL,
    PRIMARY KEY (source_id, version)
);

CREATE TABLE IF NOT EXISTS manifests (
    source_id  TEXT NOT NULL,
    version    INTEGER NOT NULL,
    manifest_json TEXT NOT NULL,
    PRIMARY KEY (source_id, version)
);

CREATE TABLE IF NOT EXISTS extraction_maps (
    source_id  TEXT NOT NULL,
    version    INTEGER NOT NULL,
    seq        INTEGER NOT NULL,
    entry_json TEXT NOT NULL,
    PRIMARY KEY (source_id, version, seq)
);
"""


class SqliteRawStorage:
    """RawStorage backed by a single SQLite database file.

    Implements the :class:`~book2skill.storage.ports.RawStorage` protocol.
    Concurrent writes from multiple processes are safe because SQLite uses
    file-level locking; for the batch orchestrator's thread-pool mode the
    default ``check_same_thread=False`` allows sharing one connection.
    """

    def __init__(self, data_home: Path | None) -> None:
        if data_home is None:
            raise ValueError("SqliteRawStorage requires a data_home path.")
        data_home.mkdir(parents=True, exist_ok=True)
        self._db_path = data_home / "book2skill.db"
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.executescript(_CREATE_SQL)
        self._conn.commit()

    def __enter__(self) -> SqliteRawStorage:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # -- RawStorage protocol ----------------------------------------------

    def save_original(
        self, source_id: str, version: int, data: bytes, original_name: str
    ) -> Path:
        """Persist original source bytes (INSERT OR REPLACE for idempotency)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO originals "
            "(source_id, version, data, original_name) "
            "VALUES (?, ?, ?, ?)",
            (source_id, version, data, original_name),
        )
        self._conn.commit()
        return self._db_path

    def load_original(self, source_id: str, version: int) -> bytes:
        """Load original source bytes, raising KeyError if absent."""
        row = self._conn.execute(
            "SELECT data FROM originals WHERE source_id = ? AND version = ?",
            (source_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"No original for source_id={source_id} version={version}"
            )
        data: bytes = row[0]
        return data

    def save_manifest(self, manifest: SourceManifest) -> Path:
        """Persist a source manifest as JSON."""
        data = manifest.model_dump(mode="json")
        self._conn.execute(
            "INSERT OR REPLACE INTO manifests "
            "(source_id, version, manifest_json) VALUES (?, ?, ?)",
            (manifest.source_id, manifest.version, json.dumps(data)),
        )
        self._conn.commit()
        return self._db_path

    def load_manifest(self, source_id: str, version: int) -> SourceManifest:
        """Load a source manifest, raising KeyError if absent."""
        row = self._conn.execute(
            "SELECT manifest_json FROM manifests "
            "WHERE source_id = ? AND version = ?",
            (source_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"No manifest for source_id={source_id} version={version}"
            )
        data = json.loads(row[0])
        return SourceManifest.model_validate(data)

    def save_extraction_map(
        self,
        source_id: str,
        version: int,
        entries: Iterable[ExtractionMapEntry],
    ) -> Path:
        """Persist extraction map entries (replaces existing for this version)."""
        self._conn.execute(
            "DELETE FROM extraction_maps "
            "WHERE source_id = ? AND version = ?",
            (source_id, version),
        )
        for seq, entry in enumerate(entries):
            self._conn.execute(
                "INSERT INTO extraction_maps "
                "(source_id, version, seq, entry_json) VALUES (?, ?, ?, ?)",
                (
                    source_id,
                    version,
                    seq,
                    json.dumps(entry.model_dump(mode="json")),
                ),
            )
        self._conn.commit()
        return self._db_path

    def load_extraction_map(
        self, source_id: str, version: int
    ) -> list[ExtractionMapEntry]:
        """Load extraction map entries in insertion order."""
        rows = self._conn.execute(
            "SELECT entry_json FROM extraction_maps "
            "WHERE source_id = ? AND version = ? ORDER BY seq",
            (source_id, version),
        ).fetchall()
        return [
            ExtractionMapEntry.model_validate(json.loads(row[0]))
            for row in rows
        ]

    def exists(self, source_id: str, version: int) -> bool:
        """Return True if a manifest exists for this source + version."""
        row = self._conn.execute(
            "SELECT 1 FROM manifests WHERE source_id = ? AND version = ?",
            (source_id, version),
        ).fetchone()
        return row is not None

    def list_versions(self, source_id: str) -> list[int]:
        """Return sorted list of persisted versions for a source."""
        rows = self._conn.execute(
            "SELECT DISTINCT version FROM manifests WHERE source_id = ? "
            "ORDER BY version",
            (source_id,),
        ).fetchall()
        return [row[0] for row in rows]

    @property
    def db_path(self) -> Path:
        """Path to the SQLite database file."""
        return self._db_path


class SqliteSchemaStorage:
    """SchemaStorage backed by SQLite (optional, P2).

    Implements the :class:`~book2skill.storage.ports.SchemaStorage` protocol.
    Collections and JSONL records are stored in the same database file.
    """

    def __init__(self, data_home: Path | None) -> None:
        if data_home is None:
            raise ValueError("SqliteSchemaStorage requires a data_home path.")
        data_home.mkdir(parents=True, exist_ok=True)
        self._db_path = data_home / "book2skill.db"
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS collections (
                collection_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                collection_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                record_json TEXT NOT NULL,
                PRIMARY KEY (collection_id, seq)
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save_collection(
        self, collection_id: str, data: dict[str, object]
    ) -> Path:
        self._conn.execute(
            "INSERT OR REPLACE INTO collections "
            "(collection_id, data_json) VALUES (?, ?)",
            (collection_id, json.dumps(data)),
        )
        self._conn.commit()
        return self._db_path

    def load_collection(self, collection_id: str) -> dict[str, object]:
        row = self._conn.execute(
            "SELECT data_json FROM collections WHERE collection_id = ?",
            (collection_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"No collection: {collection_id}")
        data: dict[str, object] = json.loads(row[0])
        return data

    def append_records(
        self,
        collection_id: str,
        records: Iterable[dict[str, object]],
    ) -> Path:
        # Find next seq.
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM records WHERE collection_id = ?",
            (collection_id,),
        ).fetchone()
        next_seq: int = row[0] + 1 if row and row[0] is not None else 0
        for record in records:
            self._conn.execute(
                "INSERT INTO records "
                "(collection_id, seq, record_json) VALUES (?, ?, ?)",
                (collection_id, next_seq, json.dumps(record)),
            )
            next_seq += 1
        self._conn.commit()
        return self._db_path


__all__ = ["SqliteRawStorage", "SqliteSchemaStorage"]
