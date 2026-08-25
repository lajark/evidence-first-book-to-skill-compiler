"""Negative controls for the generated Skill content completeness gate."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from book2skill.application.content_integrity import (
    check_generated_skill_content,
)
from book2skill.compiler import IRBuilder, SkillSpec, SkillWriter
from book2skill.domain import (
    Confidentiality,
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    SourceFormat,
    SourceManifest,
    UnitKind,
)


def _source() -> SourceManifest:
    return SourceManifest(
        source_id="source-123456",
        version=1,
        original_name="book.txt",
        content_sha256="a" * 64,
        format=SourceFormat.TXT,
        rights_confirmed=True,
        confidentiality=Confidentiality.PERSONAL,
        ingested_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
    )


def _units() -> list[KnowledgeUnit]:
    return [
        KnowledgeUnit(
            unit_id="unit-1",
            kind=UnitKind.PRINCIPLE,
            content="Validate every input before processing.",
            source_refs=[KnowledgeRef(source_id="source-123456", block_id="p-1")],
            review_status=KnowledgeStatus.CANDIDATE,
        ),
        KnowledgeUnit(
            unit_id="unit-2",
            kind=UnitKind.TECHNIQUE,
            content="Hash the normalized content\nto detect drift.",
            source_refs=[KnowledgeRef(source_id="source-123456", block_id="p-2")],
            review_status=KnowledgeStatus.CANDIDATE,
        ),
    ]


def _write_skill(root: Path) -> tuple[list[KnowledgeUnit], list[SourceManifest]]:
    units = _units()
    sources = [_source()]
    spec = SkillSpec(
        name="integrity-test",
        description="Integrity test skill.",
        use_when=["Testing generated content."],
        do_not_use_when=["No source rights."],
    )
    ir_builder = IRBuilder(units, spec)
    SkillWriter(root).write(
        ir_builder.build(),
        references=ir_builder.build_references(),
        source_manifests=sources,
    )
    return units, sources


def test_generated_content_matches_units_and_sources(tmp_path: Path) -> None:
    units, sources = _write_skill(tmp_path)

    report = check_generated_skill_content(
        tmp_path, units=units, source_manifests=sources
    )

    assert not report.blocked
    assert report.expected_unit_count == 2
    assert report.rendered_unit_count == 2
    assert report.source_count == 1


def test_integrity_report_matches_versioned_schema(tmp_path: Path) -> None:
    units, sources = _write_skill(tmp_path)
    report = check_generated_skill_content(
        tmp_path, units=units, source_manifests=sources
    )
    schema = json.loads(
        (
            Path(__file__).parents[2]
            / "schemas"
            / "content-integrity.schema.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    jsonschema.validate(report.model_dump(mode="json"), schema)


def test_missing_unit_is_blocked(tmp_path: Path) -> None:
    units, sources = _write_skill(tmp_path)
    path = tmp_path / "references" / "techniques.md"
    path.write_text("# Techniques\n", encoding="utf-8")

    report = check_generated_skill_content(
        tmp_path, units=units, source_manifests=sources
    )

    assert report.blocked
    assert report.missing_unit_ids == ["unit-2"]
    assert "source-123456 / p-2" in report.missing_source_refs


def test_truncated_or_replaced_content_is_blocked(tmp_path: Path) -> None:
    units, sources = _write_skill(tmp_path)
    path = tmp_path / "references" / "principles.md"
    text = path.read_text(encoding="utf-8").replace(
        "Validate every input before processing.", "Changed content."
    )
    path.write_text(text, encoding="utf-8")

    report = check_generated_skill_content(
        tmp_path, units=units, source_manifests=sources
    )

    assert report.blocked
    assert report.content_mismatch_unit_ids == ["unit-1"]
    assert report.content_hash_mismatch_unit_ids == ["unit-1"]


def test_duplicate_unit_is_blocked(tmp_path: Path) -> None:
    units, sources = _write_skill(tmp_path)
    path = tmp_path / "references" / "techniques.md"
    path.write_text(
        path.read_text(encoding="utf-8")
        + (
            "\n<!-- book2skill-unit-start: unit-2 -->\n## unit-2\n\n"
            "Hash the normalized content\nto detect drift.\n\n**Sources:**\n"
            "- source-123456 / p-2\n<!-- book2skill-unit-end: unit-2 -->\n"
        ),
        encoding="utf-8",
    )

    report = check_generated_skill_content(
        tmp_path, units=units, source_manifests=sources
    )

    assert report.blocked
    assert report.duplicate_unit_ids == ["unit-2"]
    assert "source-123456 / p-2" in report.duplicate_source_refs


def test_reordered_content_is_blocked(tmp_path: Path) -> None:
    units, sources = _write_skill(tmp_path)
    path = tmp_path / "references" / "techniques.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Hash the normalized content\nto detect drift.",
            "to detect drift.\nHash the normalized content",
        ),
        encoding="utf-8",
    )

    report = check_generated_skill_content(
        tmp_path, units=units, source_manifests=sources
    )

    assert report.blocked
    assert report.content_mismatch_unit_ids == ["unit-2"]
    assert report.content_hash_mismatch_unit_ids == ["unit-2"]


def test_provenance_hash_mismatch_is_blocked(tmp_path: Path) -> None:
    units, sources = _write_skill(tmp_path)
    path = tmp_path / "provenance.yml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("a" * 64, "b" * 64),
        encoding="utf-8",
    )

    report = check_generated_skill_content(
        tmp_path, units=units, source_manifests=sources
    )

    assert report.blocked
    assert report.source_hash_mismatch_ids == ["source-123456"]
