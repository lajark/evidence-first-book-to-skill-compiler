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
import shutil
import time
from dataclasses import dataclass
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

    def to_dict(self) -> dict[str, object]:
        return {
            "extension_id": self.extension_id,
            "versions": sorted(self.versions),
            "active_version": self.active_version,
            "installed_at": self.installed_at,
            "manifest": self.manifest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstallRecord:
        versions = data.get("versions", [])
        manifest = data.get("manifest", {})
        return cls(
            extension_id=str(data["extension_id"]),
            versions=[str(v) for v in versions] if isinstance(versions, list) else [],
            active_version=str(data["active_version"]),
            installed_at=str(data["installed_at"]),
            manifest=dict(manifest) if isinstance(manifest, dict) else {},
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

    # ---- persistence ------------------------------------------------------

    def _load(self) -> None:
        if not self._registry_file.is_file():
            return
        data = json.loads(self._registry_file.read_text(encoding="utf-8"))
        for ext_id, rec in data.items():
            self._records[ext_id] = InstallRecord.from_dict(rec)

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
        self.save()

    def set_active(self, extension_id: str, version: str) -> None:
        rec = self._records.get(extension_id)
        if rec is None or version not in rec.versions:
            raise KeyError(f"unknown version {version!r} for {extension_id}")
        rec.active_version = version
        self.save()

    def remove_version(self, extension_id: str, version: str) -> None:
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
        self._records.pop(extension_id, None)
        payload_root = self._versions_dir / extension_id
        if payload_root.exists():
            shutil.rmtree(payload_root)
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
