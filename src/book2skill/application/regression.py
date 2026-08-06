"""Deterministic A/B comparison for generated Skill content."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_CONTENT_ROOTS = ("SKILL.md", "references", "assets", "scripts")


class RegressionSnapshot(BaseModel):
    """Hashes of user-consumed Skill content, excluding diagnostic reports."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    skill_name: str
    file_sha256: dict[str, str]


class RegressionComparison(BaseModel):
    """Explainable difference between a historical and candidate artifact."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    baseline_skill: str
    candidate_skill: str
    content_stable: bool
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)


def capture_skill_snapshot(skill_dir: Path) -> RegressionSnapshot:
    """Capture stable hashes without including mutable reports or metadata."""
    root = Path(skill_dir)
    if not (root / "SKILL.md").is_file():
        raise ValueError(f"Skill directory has no SKILL.md: {root}")
    paths: list[Path] = [root / "SKILL.md"]
    for name in _CONTENT_ROOTS[1:]:
        directory = root / name
        if directory.is_dir():
            paths.extend(path for path in directory.rglob("*") if path.is_file())
    hashes = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths, key=lambda item: item.as_posix())
    }
    return RegressionSnapshot(skill_name=root.name, file_sha256=hashes)


def compare_skill_snapshots(
    baseline: RegressionSnapshot, candidate: RegressionSnapshot
) -> RegressionComparison:
    """Return the exact content differences between two snapshots."""
    old = baseline.file_sha256
    new = candidate.file_sha256
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(path for path in set(old) & set(new) if old[path] != new[path])
    return RegressionComparison(
        baseline_skill=baseline.skill_name,
        candidate_skill=candidate.skill_name,
        content_stable=not (added or removed or changed),
        added=added,
        removed=removed,
        changed=changed,
    )


def compare_skill_dirs(baseline_dir: Path, candidate_dir: Path) -> RegressionComparison:
    """Capture and compare two generated Skill directories."""
    return compare_skill_snapshots(
        capture_skill_snapshot(baseline_dir), capture_skill_snapshot(candidate_dir)
    )


__all__ = [
    "RegressionComparison",
    "RegressionSnapshot",
    "capture_skill_snapshot",
    "compare_skill_dirs",
    "compare_skill_snapshots",
]
