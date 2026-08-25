"""Fail-closed completeness checks for generated Skill content.

The compilation manifest proves that files are present and unchanged after
they were written.  This module proves the adjacent-stage content contract:
every active normalized unit is rendered once, with the same content and
source references, and the generated provenance ledger agrees with the
authoritative source manifests supplied by Build/Publish.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from book2skill.storage import atomic_write

CONTENT_INTEGRITY_FILENAME = "content-integrity.json"
_EXCLUDED_STATUSES = {"rejected", "superseded"}
_UNIT_START_RE = re.compile(
    r"^<!-- book2skill-unit-start: (?P<unit_id>[^\r\n]+) -->$"
)
_UNIT_END_RE = re.compile(r"^<!-- book2skill-unit-end: (?P<unit_id>[^\r\n]+) -->$")
_SOURCE_REF_RE = re.compile(
    r"^\s*-\s+(?P<source_id>[^\s/]+)\s*/\s*(?P<block_id>[^\s]+)"
)
_CONTENT_MARKERS = {"**Conditions:**", "**Exceptions:**", "**Sources:**"}


class ContentIntegrityReport(BaseModel):
    """Machine-readable completeness evidence for one generated Skill tree."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    expected_source_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    expected_unit_count: int = Field(ge=0)
    rendered_unit_count: int = Field(ge=0)
    missing_unit_ids: list[str] = Field(default_factory=list)
    extra_unit_ids: list[str] = Field(default_factory=list)
    duplicate_unit_ids: list[str] = Field(default_factory=list)
    content_mismatch_unit_ids: list[str] = Field(default_factory=list)
    content_hash_mismatch_unit_ids: list[str] = Field(default_factory=list)
    missing_source_ids: list[str] = Field(default_factory=list)
    extra_source_ids: list[str] = Field(default_factory=list)
    duplicate_source_ids: list[str] = Field(default_factory=list)
    source_hash_mismatch_ids: list[str] = Field(default_factory=list)
    missing_source_refs: list[str] = Field(default_factory=list)
    extra_source_refs: list[str] = Field(default_factory=list)
    duplicate_source_refs: list[str] = Field(default_factory=list)
    missing_files: list[str] = Field(default_factory=list)
    malformed_files: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    blocked: bool


def check_generated_skill_content(
    skill_dir: Path,
    *,
    units: Sequence[Any],
    source_manifests: Sequence[Any],
) -> ContentIntegrityReport:
    """Check generated content against authoritative in-memory inputs.

    The check deliberately fails closed on malformed UTF-8/YAML, duplicate
    headings and any missing or changed content.  ``units`` may be the full
    normalized list; rejected/superseded records are not expected in output,
    matching :class:`IRBuilder`'s rendering policy.
    """

    root = Path(skill_dir)
    expected_units = [
        unit
        for unit in units
        if str(unit.review_status) not in _EXCLUDED_STATUSES
    ]
    expected_ids = [unit.unit_id for unit in expected_units]
    expected_id_set = set(expected_ids)
    expected_by_id = {unit.unit_id: unit for unit in expected_units}

    sections: dict[str, list[str]] = defaultdict(list)
    actual_refs: list[str] = []
    malformed: list[str] = []

    reference_root = root / "references"
    for path in sorted(reference_root.glob("*.md")) if reference_root.is_dir() else []:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            malformed.append(path.relative_to(root).as_posix())
            continue
        if path.name == "provenance.md":
            continue
        lines = text.splitlines()
        markers = [
            (index, match.group("unit_id").strip())
            for index, line in enumerate(lines)
            if (match := _UNIT_START_RE.match(line)) is not None
        ]
        for marker_index, (start, unit_id) in enumerate(markers):
            end = (
                markers[marker_index + 1][0]
                if marker_index + 1 < len(markers)
                else len(lines)
            )
            if start + 1 >= len(lines) or lines[start + 1] != f"## {unit_id}":
                malformed.append(path.relative_to(root).as_posix())
                continue
            end_marker_index = next(
                (
                    index
                    for index in range(start + 2, end)
                    if (match := _UNIT_END_RE.match(lines[index])) is not None
                    and match.group("unit_id") == unit_id
                ),
                None,
            )
            if end_marker_index is None:
                malformed.append(path.relative_to(root).as_posix())
                continue
            body = lines[start + 2 : end_marker_index]
            content_lines: list[str] = []
            for line in body:
                if line.strip() in _CONTENT_MARKERS:
                    break
                content_lines.append(line)
            rendered_content = "\n".join(content_lines).strip()
            sections[unit_id].append(rendered_content)
            in_sources = False
            for line in body:
                if line.strip() == "**Sources:**":
                    in_sources = True
                    continue
                if not in_sources:
                    continue
                if match := _SOURCE_REF_RE.match(line):
                    actual_refs.append(
                        f"{match.group('source_id')} / {match.group('block_id')}"
                    )

    actual_ids = [unit_id for unit_id, values in sections.items() for _ in values]
    actual_id_set = set(actual_ids)
    missing_unit_ids = sorted(expected_id_set - actual_id_set)
    extra_unit_ids = sorted(actual_id_set - expected_id_set)
    duplicate_unit_ids = sorted(
        unit_id for unit_id, count in Counter(actual_ids).items() if count > 1
    )
    duplicate_unit_ids.extend(
        unit_id
        for unit_id, count in Counter(expected_ids).items()
        if count > 1 and unit_id not in duplicate_unit_ids
    )
    content_mismatch: list[str] = []
    for unit_id, unit in expected_by_id.items():
        rendered = sections.get(unit_id, [])
        expected_content = _normalize_content(unit.content)
        if rendered and any(
            _normalize_content(value) != expected_content for value in rendered
        ):
            content_mismatch.append(unit_id)

    expected_ref_counts = Counter(
        f"{ref.source_id} / {ref.block_id}"
        for unit in expected_units
        for ref in unit.source_refs
    )
    actual_ref_counts = Counter(actual_refs)
    missing_source_refs = sorted(
        ref
        for ref, count in expected_ref_counts.items()
        if actual_ref_counts[ref] < count
    )
    extra_source_refs = sorted(
        ref
        for ref, count in actual_ref_counts.items()
        if count > expected_ref_counts[ref]
    )
    duplicate_source_refs = sorted(
        ref
        for ref, count in actual_ref_counts.items()
        if count > expected_ref_counts[ref] and expected_ref_counts[ref] > 0
    )

    expected_source_id_counts = Counter(
        manifest.source_id for manifest in source_manifests
    )
    expected_sources = {manifest.source_id: manifest for manifest in source_manifests}
    provenance_sources, provenance_malformed = _load_provenance_sources(root)
    malformed.extend(provenance_malformed)
    actual_source_ids = [item["source_id"] for item in provenance_sources]
    actual_sources_by_id = {
        item["source_id"]: item for item in provenance_sources
    }
    expected_source_ids = set(expected_sources)
    actual_source_id_set = set(actual_source_ids)
    missing_source_ids = sorted(expected_source_ids - actual_source_id_set)
    extra_source_ids = sorted(actual_source_id_set - expected_source_ids)
    duplicate_source_ids = sorted(
        source_id
        for source_id, count in Counter(actual_source_ids).items()
        if count > 1
    )
    duplicate_source_ids.extend(
        source_id
        for source_id, count in expected_source_id_counts.items()
        if count > 1 and source_id not in duplicate_source_ids
    )
    source_hash_mismatch_ids = sorted(
        source_id
        for source_id, manifest in expected_sources.items()
        if source_id in actual_sources_by_id
        and actual_sources_by_id[source_id].get("content_sha256")
        != manifest.content_sha256
    )

    required_files = ["SKILL.md", "provenance.yml", "quality-report.md"]
    if expected_units:
        required_files.append("references")
    if not root.is_dir():
        missing_files = required_files
    else:
        missing_files = [
            name
            for name in required_files
            if not (
                (root / name).is_dir()
                if name == "references"
                else (root / name).is_file()
            )
        ]

    blocking_reasons: list[str] = []
    for label, values in (
        ("missing_unit_ids", missing_unit_ids),
        ("extra_unit_ids", extra_unit_ids),
        ("duplicate_unit_ids", duplicate_unit_ids),
        ("content_mismatch_unit_ids", sorted(set(content_mismatch))),
        ("content_hash_mismatch_unit_ids", sorted(set(content_mismatch))),
        ("missing_source_ids", missing_source_ids),
        ("extra_source_ids", extra_source_ids),
        ("duplicate_source_ids", duplicate_source_ids),
        ("source_hash_mismatch_ids", source_hash_mismatch_ids),
        ("missing_source_refs", missing_source_refs),
        ("extra_source_refs", extra_source_refs),
        ("duplicate_source_refs", duplicate_source_refs),
        ("missing_files", missing_files),
        ("malformed_files", malformed),
    ):
        if values:
            blocking_reasons.append(f"{label}: {', '.join(values)}")

    return ContentIntegrityReport(
        expected_source_count=len(expected_sources),
        source_count=len(actual_source_ids),
        expected_unit_count=len(expected_ids),
        rendered_unit_count=len(actual_ids),
        missing_unit_ids=missing_unit_ids,
        extra_unit_ids=extra_unit_ids,
        duplicate_unit_ids=sorted(set(duplicate_unit_ids)),
        content_mismatch_unit_ids=sorted(set(content_mismatch)),
        content_hash_mismatch_unit_ids=sorted(set(content_mismatch)),
        missing_source_ids=missing_source_ids,
        extra_source_ids=extra_source_ids,
        duplicate_source_ids=duplicate_source_ids,
        source_hash_mismatch_ids=source_hash_mismatch_ids,
        missing_source_refs=missing_source_refs,
        extra_source_refs=extra_source_refs,
        duplicate_source_refs=duplicate_source_refs,
        missing_files=missing_files,
        malformed_files=sorted(set(malformed)),
        blocking_reasons=blocking_reasons,
        blocked=bool(blocking_reasons),
    )


def write_content_integrity_report(
    skill_dir: Path,
    *,
    units: Sequence[Any],
    source_manifests: Sequence[Any],
) -> ContentIntegrityReport:
    """Write deterministic diagnostics before the compilation manifest."""

    report = check_generated_skill_content(
        skill_dir, units=units, source_manifests=source_manifests
    )
    atomic_write(
        Path(skill_dir) / CONTENT_INTEGRITY_FILENAME,
        report.model_dump_json(indent=2),
    )
    return report


def _load_provenance_sources(root: Path) -> tuple[list[dict[str, str]], list[str]]:
    path = root / "provenance.yml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return [], ["provenance.yml"]
    if not isinstance(data, dict) or not isinstance(data.get("sources", []), list):
        return [], ["provenance.yml"]
    records: list[dict[str, str]] = []
    malformed = False
    for item in data["sources"]:
        if not isinstance(item, dict) or not isinstance(item.get("source_id"), str):
            malformed = True
            continue
        source_id = item["source_id"]
        content_sha256 = item.get("content_sha256")
        if not isinstance(content_sha256, str):
            malformed = True
            continue
        records.append({"source_id": source_id, "content_sha256": content_sha256})
    return records, (["provenance.yml"] if malformed else [])


def _normalize_content(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(normalized.splitlines()).strip()


__all__ = [
    "CONTENT_INTEGRITY_FILENAME",
    "ContentIntegrityReport",
    "check_generated_skill_content",
    "write_content_integrity_report",
]
