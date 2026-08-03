"""File-system implementation of storage ports."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
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
    base_resolved = base.resolve()
    target = (base_resolved / Path(*parts)).resolve()
    if not target.is_relative_to(base_resolved):
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


def _stream_copy_atomic(source_path: Path, target: Path) -> None:
    """Copy *source_path* to *target* using a same-directory atomic replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_file: Path | None = None
    try:
        with source_path.open("rb") as source, tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, delete=False
        ) as tmp:
            shutil.copyfileobj(source, tmp, length=1 << 20)
            tmp.flush()
            tmp_file = Path(tmp.name)
        os.replace(tmp_file, target)
    except Exception:
        if tmp_file and tmp_file.exists():
            with contextlib.suppress(OSError):
                tmp_file.unlink()
        raise


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _render_extraction_map(entries: Iterable[ExtractionMapEntry]) -> str:
    lines = [entry.model_dump_json() for entry in entries]
    return "\n".join(lines) + "\n" if lines else ""


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

    def save_original_from_path(
        self,
        source_id: str,
        version: int,
        source_path: Path,
        original_name: str,
    ) -> Path:
        """Stream an original into Raw storage without materializing all bytes."""
        vdir = self._ensure_version_dir(source_id, version)
        original_dir = vdir / "original"
        original_dir.mkdir(exist_ok=True)
        target = original_dir / Path(original_name).name
        _stream_copy_atomic(source_path, target)
        return target

    def save_ingest_from_path(
        self,
        manifest: SourceManifest,
        source_path: Path,
        entries: Iterable[ExtractionMapEntry],
    ) -> Path:
        """Atomically create a complete Raw version from a source path.

        New versions are assembled in a same-parent staging directory and
        become visible with one rename. A matching incomplete version left by
        an interrupted older run is repaired without overwriting existing Raw
        files; conflicting content is rejected.
        """
        entry_list = list(entries)
        vdir = self._version_dir(manifest.source_id, manifest.version)
        if self.exists(manifest.source_id, manifest.version):
            return vdir
        if vdir.exists():
            self._repair_incomplete_ingest(manifest, source_path, entry_list)
            return vdir

        vdir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{manifest.version}-", dir=vdir.parent)
        )
        try:
            original = staging / "original" / Path(
                manifest.original_name or source_path.name
            ).name
            _stream_copy_atomic(source_path, original)
            atomic_write(
                staging / "manifest.json", manifest.model_dump_json(indent=2)
            )
            atomic_write(
                staging / "extraction-map.jsonl",
                _render_extraction_map(entry_list),
            )
            os.replace(staging, vdir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return vdir

    def _repair_incomplete_ingest(
        self,
        manifest: SourceManifest,
        source_path: Path,
        entries: list[ExtractionMapEntry],
    ) -> None:
        """Complete a matching interrupted version without replacing Raw data."""
        vdir = self._version_dir(manifest.source_id, manifest.version)
        manifest_path = vdir / "manifest.json"
        stored_manifest: SourceManifest | None = None
        if manifest_path.exists():
            stored_manifest = self.load_manifest(manifest.source_id, manifest.version)
            if (
                stored_manifest.content_sha256 != manifest.content_sha256
                or stored_manifest.format != manifest.format
            ):
                raise ValueError(
                    "incomplete Raw version conflicts with source manifest"
                )

        original_name = (
            stored_manifest.original_name
            if stored_manifest and stored_manifest.original_name
            else manifest.original_name or source_path.name
        )
        original_path = vdir / "original" / Path(original_name).name
        if original_path.exists():
            if _sha256_path(original_path) != manifest.content_sha256:
                raise ValueError("incomplete Raw version contains conflicting original")
        else:
            self.save_original_from_path(
                manifest.source_id,
                manifest.version,
                source_path,
                original_name,
            )

        if stored_manifest is None:
            self.save_manifest(manifest)

        map_path = vdir / "extraction-map.jsonl"
        if map_path.exists():
            stored_entries = self.load_extraction_map(
                manifest.source_id, manifest.version
            )
            if stored_entries != entries:
                raise ValueError("incomplete Raw version contains conflicting map")
        else:
            self.save_extraction_map(manifest.source_id, manifest.version, entries)

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
        atomic_write(path, _render_extraction_map(entries))
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
        vdir = self._version_dir(source_id, version)
        original_dir = vdir / "original"
        return (
            (vdir / "manifest.json").is_file()
            and (vdir / "extraction-map.jsonl").is_file()
            and original_dir.is_dir()
            and any(child.is_file() for child in original_dir.iterdir())
        )

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
