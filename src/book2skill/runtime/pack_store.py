"""Persistent candidate/release storage for task-centered Asset Packs.

The Pack publisher remains responsible for governance and regression checks.
This adapter persists those immutable decisions without rebuilding a whole
Generated Skill tree: releases are content-addressed by their pack hash and a
small active pointer is swapped atomically.  A failed pointer swap therefore
leaves the previously active Pack selectable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.runtime.pack_update import (
    AssetPackRelease,
    PackCandidate,
    PackPublisher,
    PackRollback,
    _version_tuple,
)
from book2skill.storage import atomic_write

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SCHEMA_VERSION = 1


class PackStoreError(DomainError):
    """Stable error for persistent Pack state and pointer failures."""

    def __init__(
        self,
        code: ErrorCode,
        pack_id: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code,
            pack_id,
            message,
            recovery=(
                "Keep the current active Pack and repair the staged state "
                "before retrying."
            ),
            details=details,
        )


def _safe_segment(value: str, *, field: str, pack_id: str) -> str:
    if not _SAFE_SEGMENT.fullmatch(value):
        raise PackStoreError(
            ErrorCode.PACK_UPDATE_INVALID,
            pack_id,
            f"{field} contains an unsafe path segment",
            details={field: value},
        )
    return value


class PersistentPackStore:
    """Store Pack candidates, immutable releases and an active pointer.

    Layout under ``root`` is ``<pack_id>/{candidates,releases,active.json}``.
    A release file can only be written once: an existing path with different
    canonical content is treated as tampering rather than overwritten.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _pack_dir(self, pack_id: str) -> Path:
        _safe_segment(pack_id, field="pack_id", pack_id=pack_id)
        return self.root / pack_id

    def _candidate_path(self, pack_id: str, candidate_id: str) -> Path:
        _safe_segment(candidate_id, field="candidate_id", pack_id=pack_id)
        return self._pack_dir(pack_id) / "candidates" / f"{candidate_id}.json"

    def _release_path(self, pack_id: str, version: str) -> Path:
        _safe_segment(version, field="version", pack_id=pack_id)
        return self._pack_dir(pack_id) / "releases" / f"{version}.json"

    def _active_path(self, pack_id: str) -> Path:
        return self._pack_dir(pack_id) / "active.json"

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        atomic_write(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def save_candidate(self, candidate: PackCandidate) -> None:
        """Persist a candidate/review decision without activating it."""

        path = self._candidate_path(candidate.base_pack_id, candidate.candidate_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(
            path,
            {
                "schema_version": _SCHEMA_VERSION,
                "kind": "pack_candidate",
                "candidate": candidate.model_dump(mode="json"),
            },
        )

    def load_candidate(self, pack_id: str, candidate_id: str) -> PackCandidate:
        path = self._candidate_path(pack_id, candidate_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(record, dict)
                or record.get("schema_version") != _SCHEMA_VERSION
                or record.get("kind") != "pack_candidate"
            ):
                raise ValueError("unsupported candidate record")
            candidate = PackCandidate.model_validate(record["candidate"])
            if (
                candidate.base_pack_id != pack_id
                or candidate.candidate_id != candidate_id
            ):
                raise ValueError("candidate identity does not match its path")
            return candidate
        except FileNotFoundError as exc:
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_INVALID,
                pack_id,
                "Pack candidate was not found",
                details={"candidate_id": candidate_id},
            ) from exc
        except ValueError as exc:
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_INVALID,
                pack_id,
                "Pack candidate is not valid JSON",
                details={"candidate_id": candidate_id},
            ) from exc

    def save_release(self, release: AssetPackRelease) -> None:
        """Persist one immutable production release, refusing hash drift."""

        pack_id = release.pack_id
        if release.pack_hash != release.compute_hash():
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_INVALID,
                pack_id,
                "Pack release hash is stale",
                details={"version": release.version},
            )
        path = self._release_path(pack_id, release.version)
        payload = json.dumps(
            {
                "schema_version": _SCHEMA_VERSION,
                "kind": "pack_release",
                "release": release.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != payload:
                raise PackStoreError(
                    ErrorCode.PACK_UPDATE_INVALID,
                    pack_id,
                    "Immutable Pack release content changed",
                    details={"version": release.version},
                )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, payload)

    def load_release(self, pack_id: str, version: str) -> AssetPackRelease:
        path = self._release_path(pack_id, version)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(record, dict)
                or record.get("schema_version") != _SCHEMA_VERSION
                or record.get("kind") != "pack_release"
            ):
                raise ValueError("unsupported release record")
            release = AssetPackRelease.model_validate(record["release"])
        except FileNotFoundError as exc:
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_INVALID,
                pack_id,
                "Pack release was not found",
                details={"version": version},
            ) from exc
        except ValueError as exc:
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_INVALID,
                pack_id,
                "Pack release is not valid JSON",
                details={"version": version},
            ) from exc
        if release.pack_id != pack_id or release.version != version:
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_INVALID,
                pack_id,
                "Pack release identity does not match its path",
                details={"version": version},
            )
        if release.pack_hash != release.compute_hash():
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_INVALID,
                pack_id,
                "Pack release hash does not match its content",
                details={"version": version},
            )
        return release

    def list_releases(self, pack_id: str) -> list[AssetPackRelease]:
        directory = self._pack_dir(pack_id) / "releases"
        if not directory.exists():
            return []
        releases = [
            self.load_release(pack_id, path.stem)
            for path in directory.glob("*.json")
        ]
        return sorted(releases, key=lambda item: _version_tuple(item.version))

    def active(self, pack_id: str) -> AssetPackRelease | None:
        path = self._active_path(pack_id)
        if not path.exists():
            return None
        try:
            pointer = json.loads(path.read_text(encoding="utf-8"))
            if pointer.get("schema_version") != _SCHEMA_VERSION:
                raise ValueError("unsupported active pointer schema")
            if pointer.get("pack_id") != pack_id:
                raise ValueError("active pointer pack mismatch")
            release = self.load_release(pack_id, str(pointer["version"]))
            if release.pack_hash != pointer.get("pack_hash"):
                raise ValueError("active pointer hash mismatch")
            return release
        except (KeyError, TypeError, ValueError) as exc:
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_INVALID,
                pack_id,
                "Active Pack pointer is invalid",
            ) from exc

    def activate(
        self,
        release: AssetPackRelease,
        *,
        expected_base_version: str | None = None,
    ) -> AssetPackRelease:
        """Atomically switch active Pack after an optional optimistic check."""

        self.save_release(release)
        current = self.active(release.pack_id)
        if expected_base_version is not None and (
            current is None or current.version != expected_base_version
        ):
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_CONTRACT_MISMATCH,
                release.pack_id,
                "Active Pack changed since the candidate was based",
                details={
                    "expected_base_version": expected_base_version,
                    "actual_base_version": current.version if current else None,
                },
            )
        pointer = {
            "schema_version": _SCHEMA_VERSION,
            "pack_id": release.pack_id,
            "version": release.version,
            "pack_hash": release.pack_hash,
        }
        path = self._active_path(release.pack_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(path, pointer)
        return release

    def publish_incremental(
        self,
        base: AssetPackRelease,
        candidate: PackCandidate,
        *,
        version: str,
        regression: Callable[[AssetPackRelease], bool],
    ) -> AssetPackRelease:
        """Publish and activate a Pack without rebuilding a Skill tree."""

        current = self.active(base.pack_id)
        if current is not None and current.pack_hash != base.pack_hash:
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_CONTRACT_MISMATCH,
                base.pack_id,
                "Publish base is not the active Pack",
                details={
                    "active_version": current.version,
                    "base_version": base.version,
                },
            )
        release = PackPublisher().publish(
            base, candidate, version=version, regression=regression
        )
        return self.activate(release, expected_base_version=base.version)

    def rollback(self, pack_id: str, target_version: str) -> PackRollback:
        current = self.active(pack_id)
        if current is None:
            raise PackStoreError(
                ErrorCode.PACK_UPDATE_ROLLBACK_INVALID,
                pack_id,
                "Cannot rollback a Pack without an active release",
            )
        target = self.load_release(pack_id, target_version)
        rollback = PackPublisher().rollback(current, target)
        self.activate(target, expected_base_version=current.version)
        return rollback
