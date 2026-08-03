"""Storage port interfaces.

Ports define the storage contract. Implementations live in
`book2skill.storage.file_storage`.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from book2skill.domain import ExtractionMapEntry, SourceManifest


class RawStorage(Protocol):
    """Persists raw sources, manifests and extraction maps."""

    def save_original(
        self, source_id: str, version: int, data: bytes, original_name: str
    ) -> Path:
        """Persist the original source bytes.

        Returns the path where the file was stored.
        """
        ...

    def load_original(self, source_id: str, version: int) -> bytes:
        """Load the original source bytes."""
        ...

    def save_manifest(self, manifest: SourceManifest) -> Path:
        """Persist a source manifest."""
        ...

    def load_manifest(self, source_id: str, version: int) -> SourceManifest:
        """Load a source manifest."""
        ...

    def save_extraction_map(
        self,
        source_id: str,
        version: int,
        entries: Iterable[ExtractionMapEntry],
    ) -> Path:
        """Persist an extraction map as JSONL."""
        ...

    def load_extraction_map(
        self, source_id: str, version: int
    ) -> list[ExtractionMapEntry]:
        """Load an extraction map from JSONL."""
        ...

    def exists(self, source_id: str, version: int) -> bool:
        """Return True if original, manifest and extraction map all exist."""
        ...

    def list_versions(self, source_id: str) -> list[int]:
        """Return sorted list of persisted versions for a source."""
        ...


class SchemaStorage(Protocol):
    """Persists schema/collection data (skeleton for M3)."""

    def save_collection(self, collection_id: str, data: dict[str, object]) -> Path:
        """Persist a collection document."""
        ...

    def load_collection(self, collection_id: str) -> dict[str, object]:
        """Load a collection document."""
        ...

    def append_records(
        self, collection_id: str, records: Iterable[dict[str, object]]
    ) -> Path:
        """Append records to a JSONL collection."""
        ...


class WikiStorage(Protocol):
    """Persists generated wiki/skill output (skeleton for M3)."""

    def write_skill(self, skill_slug: str, content: str) -> Path:
        """Write skill content to the wiki."""
        ...

    def read_skill(self, skill_slug: str) -> str:
        """Read skill content from the wiki."""
        ...
