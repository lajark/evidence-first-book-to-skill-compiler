"""Canonical manifest for one compiled Skill artifact.

Build and Publish both produce the same on-disk contract.  The manifest is
written last in the staging tree and records hashes for every other output
file, giving Update recovery one stable point with which to correlate the
Skill tree and the Schema transaction.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from book2skill.compiler import SkillSpec
from book2skill.domain import KnowledgeUnit, SourceManifest
from book2skill.storage import atomic_write

ARTIFACT_FILENAME = "compilation-artifact.json"


class CompilationArtifact(BaseModel):
    """Versioned, content-addressed view of a compiled Skill directory."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str = Field(..., min_length=16)
    collection_id: str
    skill_name: str
    unit_ids: list[str]
    source_ids: list[str]
    files: dict[str, str]


def write_compilation_artifact(
    skill_dir: Path,
    *,
    collection_id: str,
    spec: SkillSpec,
    units: list[KnowledgeUnit],
    source_manifests: list[SourceManifest],
) -> CompilationArtifact:
    """Hash the staged outputs and atomically write the canonical manifest."""
    files = _file_hashes(skill_dir)
    unit_ids = sorted(unit.unit_id for unit in units)
    source_ids = sorted(manifest.source_id for manifest in source_manifests)
    identity = {
        "schema_version": 1,
        "collection_id": collection_id,
        "skill_name": spec.name,
        "unit_ids": unit_ids,
        "source_ids": source_ids,
        "files": files,
    }
    artifact_id = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    ).hexdigest()
    artifact = CompilationArtifact(
        artifact_id=artifact_id,
        collection_id=collection_id,
        skill_name=spec.name,
        unit_ids=unit_ids,
        source_ids=source_ids,
        files=files,
    )
    atomic_write(
        skill_dir / ARTIFACT_FILENAME,
        artifact.model_dump_json(indent=2),
    )
    return artifact


def load_compilation_artifact(skill_dir: Path) -> CompilationArtifact | None:
    path = Path(skill_dir) / ARTIFACT_FILENAME
    if not path.is_file():
        return None
    try:
        return CompilationArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def verify_compilation_artifact(
    skill_dir: Path,
    artifact: CompilationArtifact,
    *,
    units: list[KnowledgeUnit] | None = None,
) -> bool:
    """Verify manifest identity, output inventory and optional unit IDs."""
    actual = _file_hashes(skill_dir)
    if actual != artifact.files:
        return False
    identity = {
        "schema_version": artifact.schema_version,
        "collection_id": artifact.collection_id,
        "skill_name": artifact.skill_name,
        "unit_ids": artifact.unit_ids,
        "source_ids": artifact.source_ids,
        "files": artifact.files,
    }
    expected_id = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    ).hexdigest()
    if expected_id != artifact.artifact_id:
        return False
    return units is None or sorted(unit.unit_id for unit in units) == artifact.unit_ids


def _file_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not root.is_dir():
        return hashes
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != ARTIFACT_FILENAME:
            hashes[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return hashes


__all__ = [
    "ARTIFACT_FILENAME",
    "CompilationArtifact",
    "load_compilation_artifact",
    "verify_compilation_artifact",
    "write_compilation_artifact",
]
