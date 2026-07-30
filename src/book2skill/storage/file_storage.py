"""File-system implementation of storage ports."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from book2skill.domain import (
    ExtractionMapEntry,
    SourceManifest,
)

from .errors import StorageNotFoundError, StoragePathError


def resolve_within(base: Path, *parts: str) -> Path:
    """Resolve a path under base and reject path traversal.

    Args:
        base: Root directory that the resolved path must be inside.
        *parts: Path parts to join to base.

    Returns:
        Absolute path under base.

    Raises:
        StoragePathError: If the resolved path escapes base.
    """
    target = (base / Path(*parts)).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise StoragePathError(target)
    return target


def atomic_write(path: Path, content: str | bytes) -> None:
    """Atomically write content to path using a temporary file.

    On failure the temporary file is removed. The temporary file is created
    in the same directory as the target to avoid cross-device renames.
    """
    mode = "wb" if isinstance(content, bytes) else "w"
    encoding: str | None = "utf-8" if isinstance(content, str) else None
    tmp_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode=mode,
            dir=path.parent,
            delete=False,
            encoding=encoding,
        ) as tmp:
            tmp.write(content)
            tmp.flush()
            tmp_file = Path(tmp.name)
        os.replace(tmp_file, path)
    except Exception:
        if tmp_file and tmp_file.exists():
            with contextlib.suppress(OSError):
                tmp_file.unlink()
        raise


class FileRawStorage:
    """File-system implementation of the RawStorage port."""

    def __init__(self, data_home: Path) -> None:
        self.data_home = data_home.resolve()
        self.raw_root = self.data_home / "raw"

    def _version_dir(self, source_id: str, version: int) -> Path:
        return resolve_within(self.raw_root, source_id, str(version))

    def _ensure_version_dir(self, source_id: str, version: int) -> Path:
        vdir = self._version_dir(source_id, version)
        vdir.mkdir(parents=True, exist_ok=True)
        return vdir

    def save_original(
        self, source_id: str, version: int, data: bytes, original_name: str
    ) -> Path:
        vdir = self._ensure_version_dir(source_id, version)
        original_dir = vdir / "original"
        original_dir.mkdir(exist_ok=True)
        target = original_dir / Path(original_name).name
        atomic_write(target, data)
        return target

    def load_original(self, source_id: str, version: int) -> bytes:
        manifest = self.load_manifest(source_id, version)
        original_dir = self._version_dir(source_id, version) / "original"
        target = original_dir / Path(manifest.original_name or "unknown").name
        if not target.exists():
            raise StorageNotFoundError(source_id, str(target))
        return target.read_bytes()

    def save_manifest(self, manifest: SourceManifest) -> Path:
        vdir = self._ensure_version_dir(manifest.source_id, manifest.version)
        path = vdir / "manifest.json"
        atomic_write(path, manifest.model_dump_json(indent=2))
        return path

    def load_manifest(self, source_id: str, version: int) -> SourceManifest:
        path = self._version_dir(source_id, version) / "manifest.json"
        if not path.exists():
            raise StorageNotFoundError(source_id, str(path))
        data = json.loads(path.read_text(encoding="utf-8"))
        return SourceManifest(**data)

    def save_extraction_map(
        self,
        source_id: str,
        version: int,
        entries: Iterable[ExtractionMapEntry],
    ) -> Path:
        vdir = self._ensure_version_dir(source_id, version)
        path = vdir / "extraction-map.jsonl"
        lines = [entry.model_dump_json() for entry in entries]
        atomic_write(path, "\n".join(lines) + "\n" if lines else "")
        return path

    def load_extraction_map(
        self, source_id: str, version: int
    ) -> list[ExtractionMapEntry]:
        path = self._version_dir(source_id, version) / "extraction-map.jsonl"
        if not path.exists():
            return []
        entries: list[ExtractionMapEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entries.append(ExtractionMapEntry(**json.loads(line)))
        return entries

    def exists(self, source_id: str, version: int) -> bool:
        return self._version_dir(source_id, version).exists()

    def list_versions(self, source_id: str) -> list[int]:
        source_dir = self.raw_root / source_id
        if not source_dir.exists():
            return []
        versions: list[int] = []
        for child in source_dir.iterdir():
            if child.is_dir() and child.name.isdigit():
                versions.append(int(child.name))
        return sorted(versions)


class FileSchemaStorage:
    """File-system implementation of the SchemaStorage port (skeleton)."""

    def __init__(self, data_home: Path) -> None:
        self.data_home = data_home.resolve()
        self.schema_root = self.data_home / "schema"

    def _collection_dir(self, collection_id: str) -> Path:
        return resolve_within(self.schema_root, collection_id)

    def save_collection(self, collection_id: str, data: dict[str, object]) -> Path:
        path = self._collection_dir(collection_id) / "collection.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(data, indent=2, default=str))
        return path

    def load_collection(self, collection_id: str) -> dict[str, object]:
        path = self._collection_dir(collection_id) / "collection.json"
        if not path.exists():
            raise StorageNotFoundError(collection_id, str(path))
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def append_records(
        self, collection_id: str, records: Iterable[dict[str, object]]
    ) -> Path:
        path = self._collection_dir(collection_id) / "records.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(record, default=str) for record in records]
        if path.exists():
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        else:
            atomic_write(path, "\n".join(lines) + "\n")
        return path


class FileWikiStorage:
    """File-system implementation of the WikiStorage port (skeleton)."""

    def __init__(self, data_home: Path) -> None:
        self.data_home = data_home.resolve()
        self.wiki_root = self.data_home / "wiki"

    def _skill_dir(self, skill_slug: str) -> Path:
        return resolve_within(self.wiki_root, skill_slug)

    def write_skill(self, skill_slug: str, content: str) -> Path:
        skill_dir = self._skill_dir(skill_slug)
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        atomic_write(path, content)
        return path

    def read_skill(self, skill_slug: str) -> str:
        path = self._skill_dir(skill_slug) / "SKILL.md"
        if not path.exists():
            raise StorageNotFoundError(skill_slug, str(path))
        return path.read_text(encoding="utf-8")
