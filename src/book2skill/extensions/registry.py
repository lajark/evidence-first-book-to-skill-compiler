"""Extension installation registry.

The registry records which extensions and versions are installed, which one is
active, where their payloads live, and captures pre-change snapshots so an
upgrade or uninstall can be rolled back atomically.

Layout under *registry_root*::

    registry.json                    # { id: Record }
    versions/<id>/<version>/         # one directory per installed payload
    snapshots/<id>/<timestamp>.json  # JsonSnapshot for rollback
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from book2skill.sdk.models import ExtensionManifest

_REGISTRY_FILENAME = "registry.json"


@dataclass
class InstallRecord:
    """One extension's installation state."""

    extension_id: str
    versions: list[str]
    active_version: str
    installed_at: str
    manifest: dict[str, object]
    # Per-version payload inventory captured after a verified install.  Older
    # registries may not have this field; callers must handle that case.
    payload_hashes: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "extension_id": self.extension_id,
            "versions": sorted(self.versions),
            "active_version": self.active_version,
            "installed_at": self.installed_at,
            "manifest": self.manifest,
            "payload_hashes": self.payload_hashes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstallRecord:
        versions = data.get("versions", [])
        manifest = data.get("manifest", {})
        payload_hashes = data.get("payload_hashes", {})
        normalized_hashes: dict[str, dict[str, str]] = {}
        if isinstance(payload_hashes, dict):
            for version, hashes in payload_hashes.items():
                if isinstance(hashes, dict):
                    normalized_hashes[str(version)] = {
                        str(path): str(digest)
                        for path, digest in hashes.items()
                    }
        return cls(
            extension_id=str(data["extension_id"]),
            versions=[str(v) for v in versions] if isinstance(versions, list) else [],
            active_version=str(data["active_version"]),
            installed_at=str(data["installed_at"]),
            manifest=dict(manifest) if isinstance(manifest, dict) else {},
            payload_hashes=normalized_hashes,
        )


class ExtensionRegistry:
    """JSON-backed store of installed extensions and their payloads."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._versions_dir = self._root / "versions"
        self._snapshots_dir = self._root / "snapshots"
        self._registry_file = self._root / _REGISTRY_FILENAME
        self._records: dict[str, InstallRecord] = {}
        self._load()
        self._repair_records()

    # ---- persistence ------------------------------------------------------

    def _load(self) -> None:
        self._records.clear()
        if not self._registry_file.is_file():
            return
        data = json.loads(self._registry_file.read_text(encoding="utf-8"))
        for ext_id, rec in data.items():
            self._records[ext_id] = InstallRecord.from_dict(rec)

    def _repair_records(self) -> None:
        """Reconcile registry records with payloads after an interrupted write.

        Registry JSON writes are atomic, but payload copying and registry
        persistence are separate filesystem operations. On startup, discard
        missing versions from records and move ``active_version`` to the
        newest remaining payload. Orphan payload directories are retained for
        manual diagnosis rather than deleted implicitly.
        """
        changed = False
        for extension_id in list(self._records):
            record = self._records[extension_id]
            available = [
                version
                for version in record.versions
                if self.payload_dir(extension_id, version).is_dir()
            ]
            if not available:
                del self._records[extension_id]
                changed = True
                continue
            if sorted(available) != sorted(record.versions):
                record.versions = available
                changed = True
            if record.active_version not in available:
                record.active_version = max(available)
                changed = True
        if changed:
            self.save()

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        """Serialize registry mutations across processes.

        The lock is a same-directory create-exclusive marker. A stale marker
        older than one minute is safe to remove because it cannot represent an
        active mutation under the bounded write operations below.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        lock_path = self._root / ".registry.lock"
        acquired = False
        try:
            for _ in range(1000):
                try:
                    fd = os.open(
                        lock_path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    )
                    os.close(fd)
                    acquired = True
                    break
                except FileExistsError:
                    try:
                        if time.time() - lock_path.stat().st_mtime > 60:
                            lock_path.unlink()
                            continue
                    except FileNotFoundError:
                        continue
                    time.sleep(0.01)
            if not acquired:
                raise TimeoutError("timed out waiting for extension registry lock")
            self._load()
            yield
        finally:
            if acquired:
                with suppress(FileNotFoundError):
                    lock_path.unlink()

    @contextmanager
    def mutation(self) -> Iterator[None]:
        """Hold the registry mutation lock across a compound lifecycle step.

        Extension activation needs the registry state and its durable journal to
        move together.  The public context keeps that operation serialized
        without exposing the lock implementation to the installer.
        """
        with self._mutation_lock():
            yield

    def save(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        payload = {
            ext_id: rec.to_dict() for ext_id, rec in sorted(self._records.items())
        }
        _atomic_write(
            self._registry_file, json.dumps(payload, indent=2, sort_keys=True)
        )

    # ---- queries ----------------------------------------------------------

    def installed_ids(self) -> list[str]:
        return sorted(self._records)

    def is_installed(self, extension_id: str) -> bool:
        return extension_id in self._records

    def versions(self, extension_id: str) -> list[str]:
        rec = self._records.get(extension_id)
        return sorted(rec.versions) if rec else []

    def active_version(self, extension_id: str) -> str | None:
        rec = self._records.get(extension_id)
        return rec.active_version if rec else None

    def record(self, extension_id: str) -> InstallRecord | None:
        return self._records.get(extension_id)

    def payload_dir(self, extension_id: str, version: str) -> Path:
        return self._versions_dir / extension_id / version

    def active_payload_dir(self, extension_id: str) -> Path | None:
        version = self.active_version(extension_id)
        if version is None:
            return None
        return self.payload_dir(extension_id, version)

    # ---- mutations --------------------------------------------------------

    def add_version(
        self,
        manifest: ExtensionManifest,
        payload_dir: Path,
        *,
        installed_at: str | None = None,
    ) -> None:
        with self._mutation_lock():
            self._add_version_unlocked(
                manifest, payload_dir, installed_at=installed_at
            )

    def _add_version_unlocked(
        self,
        manifest: ExtensionManifest,
        payload_dir: Path,
        *,
        installed_at: str | None = None,
    ) -> None:
        """Register *manifest* whose payload already lives at *payload_dir*.

        Side-by-side versions are allowed; the newest installed version does
        not automatically become active (the installer decides).
        """
        ext_id = manifest.extension_id
        version = manifest.version
        existing = self._records.get(ext_id)
        if existing is None:
            self._records[ext_id] = InstallRecord(
                extension_id=ext_id,
                versions=[version],
                active_version=version,
                installed_at=installed_at or _now(),
                manifest=manifest.model_dump(mode="json"),
                payload_hashes={},
            )
        else:
            if version not in existing.versions:
                existing.versions.append(version)
            existing.manifest = manifest.model_dump(mode="json")
        # Persist the payload directory alongside the record.
        target = self.payload_dir(ext_id, version)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(payload_dir, target)
        existing_record = self._records[ext_id]
        existing_record.payload_hashes[version] = self._read_version_payload_hashes(
            ext_id, version
        )
        self.save()

    def set_active(self, extension_id: str, version: str) -> None:
        with self._mutation_lock():
            self._set_active_unlocked(extension_id, version)

    def _set_active_unlocked(self, extension_id: str, version: str) -> None:
        rec = self._records.get(extension_id)
        if rec is None or version not in rec.versions:
            raise KeyError(f"unknown version {version!r} for {extension_id}")
        rec.active_version = version
        self.save()

    def remove_version(self, extension_id: str, version: str) -> None:
        with self._mutation_lock():
            self._remove_version_unlocked(extension_id, version)

    def _remove_version_unlocked(self, extension_id: str, version: str) -> None:
        rec = self._records.get(extension_id)
        if rec is None:
            return
        rec.versions = [v for v in rec.versions if v != version]
        payload = self.payload_dir(extension_id, version)
        if payload.exists():
            shutil.rmtree(payload)
        if not rec.versions:
            del self._records[extension_id]
        elif rec.active_version == version:
            rec.active_version = max(rec.versions)
        self.save()

    def remove(self, extension_id: str) -> None:
        """Remove all versions of an extension (payload + record)."""
        with self._mutation_lock():
            self._remove_unlocked(extension_id)

    def _remove_unlocked(self, extension_id: str) -> None:
        self._records.pop(extension_id, None)
        payload_root = self._versions_dir / extension_id
        if payload_root.exists():
            shutil.rmtree(payload_root)
        self.save()

    def restore_record(
        self, extension_id: str, record: dict[str, object] | None
    ) -> None:
        """Restore one serialized record during durable transaction rollback."""
        with self._mutation_lock():
            self._restore_record_unlocked(extension_id, record)

    def _restore_record_unlocked(
        self, extension_id: str, record: dict[str, object] | None
    ) -> None:
        if record is None:
            self._records.pop(extension_id, None)
        else:
            self._records[extension_id] = InstallRecord.from_dict(record)
        self.save()

    # ---- snapshots --------------------------------------------------------

    def snapshot(self, extension_id: str) -> dict[str, object]:
        """Serialize the current install state for later rollback."""
        rec = self._records.get(extension_id)
        return {
            "extension_id": extension_id,
            "record": rec.to_dict() if rec else None,
            "versions": self._read_payload_hashes(extension_id),
        }

    def write_snapshot(self, extension_id: str) -> Path:
        """Persist a timestamped snapshot and return its path."""
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        dest = self._snapshots_dir / extension_id
        snap = self.snapshot(extension_id)
        _atomic_write(
            dest.with_suffix(f".{int(time.time() * 1000)}.json"),
            json.dumps(snap, indent=2, sort_keys=True),
        )
        return dest

    def _read_payload_hashes(self, extension_id: str) -> dict[str, str]:
        """Map every payload file path to its SHA-256 (used by rollback)."""
        root = self._versions_dir / extension_id
        hashes: dict[str, str] = {}
        if not root.exists():
            return hashes
        for p in root.rglob("*"):
            if p.is_file():
                hashes[p.relative_to(root).as_posix()] = _file_sha256(p.read_bytes())
        return hashes

    def _read_version_payload_hashes(
        self, extension_id: str, version: str
    ) -> dict[str, str]:
        root = self.payload_dir(extension_id, version)
        hashes: dict[str, str] = {}
        if not root.exists():
            return hashes
        for path in root.rglob("*"):
            if path.is_file():
                hashes[path.relative_to(root).as_posix()] = _file_sha256(
                    path.read_bytes()
                )
        return hashes


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _file_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
