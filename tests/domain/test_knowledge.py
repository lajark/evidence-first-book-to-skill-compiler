"""Tests for the canonical knowledge-layer domain models and operations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import validate
from jsonschema.validators import Draft202012Validator
from pydantic import ValidationError

from book2skill.domain import (
    ConflictRecord,
    ConflictStatus,
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    ReviewItem,
    UnitKind,
    build_supersession,
    cluster_units,
    detect_conflicts,
    latest_record,
)


def _load_schema(name: str) -> dict:
    return json.loads(Path("schemas", name).read_text(encoding="utf-8"))


def _ref(source_id: str = "src-1", block_id: str = "blk-1") -> KnowledgeRef:
    return KnowledgeRef(source_id=source_id, block_id=block_id, quote="q")


def _unit(
    *,
    unit_id: str = "ku-1",
    kind: UnitKind = UnitKind.PRINCIPLE,
    content: str = "Always write tests before shipping code.",
    review_status: KnowledgeStatus = KnowledgeStatus.CANDIDATE,
    record_version: int = 1,
    supersedes: str | None = None,
) -> KnowledgeUnit:
    return KnowledgeUnit(
        unit_id=unit_id,
        kind=kind,
        content=content,
        source_refs=[_ref()],
        confidence=0.8,
        review_status=review_status,
        record_version=record_version,
        supersedes=supersedes,
    )


class TestKnowledgeUnitModel:
    def test_round_trip_and_schema_validation(self) -> None:
        unit = _unit()
        data = json.loads(unit.model_dump_json())
        schema = _load_schema("knowledge-unit.schema.json")
        validate(instance=data, schema=schema, cls=Draft202012Validator)
        assert data["schema_version"] == 1
        assert data["review_status"] == "candidate"
        assert data["kind"] == "principle"

    def test_optional_fields_default(self) -> None:
        unit = _unit()
        assert unit.conditions == []
        assert unit.exceptions == []
        assert unit.supersedes is None
        assert unit.record_version == 1

    def test_rejects_empty_content(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeUnit(
                unit_id="ku-x",
                kind=UnitKind.TERM,
                content="",
                source_refs=[_ref()],
                review_status=KnowledgeStatus.CANDIDATE,
            )

    def test_rejects_empty_source_refs(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeUnit(
                unit_id="ku-x",
                kind=UnitKind.TERM,
                content="A term.",
                source_refs=[],
                review_status=KnowledgeStatus.CANDIDATE,
            )

    def test_rejects_invalid_confidence(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeUnit(
                unit_id="ku-x",
                kind=UnitKind.TERM,
                content="A term.",
                source_refs=[_ref()],
                confidence=1.5,
                review_status=KnowledgeStatus.CANDIDATE,
            )

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeUnit(
                unit_id="ku-x",
                kind=UnitKind.TERM,
                content="A term.",
                source_refs=[_ref()],
                review_status=KnowledgeStatus.CANDIDATE,
                surprise="no",  # type: ignore[call-arg]
            )

    def test_all_unit_kinds_round_trip(self) -> None:
        for kind in UnitKind:
            unit = _unit(kind=kind)
            assert unit.kind == kind.value  # use_enum_values -> plain str


class TestConflictRecordAndReviewItem:
    def test_conflict_record_construction(self) -> None:
        rec = ConflictRecord(
            conflict_id="c-0",
            unit_ids=["ku-1", "ku-2"],
            description="Divergent principles.",
            status=ConflictStatus.OPEN,
            conditions=["when scaling"],
        )
        assert rec.status == "open"
        assert rec.conditions == ["when scaling"]

    def test_conflict_record_rejects_single_unit(self) -> None:
        with pytest.raises(ValidationError):
            ConflictRecord(
                conflict_id="c-0",
                unit_ids=["ku-1"],
                description="x",
                status=ConflictStatus.OPEN,
            )

    def test_review_item_optional_fields(self) -> None:
        item = ReviewItem(
            item_id="rv-1",
            ref_type="candidate_unit",
            ref_id="ku-1",
            reason="low_confidence",
            severity="warning",
        )
        assert item.disposition is None
        assert item.reviewer is None
        assert item.severity == "warning"

    def test_review_item_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            ReviewItem(
                item_id="rv-1",
                ref_type="candidate_unit",
                ref_id="ku-1",
                reason="low_confidence",
                severity="warning",
                bogus=True,  # type: ignore[call-arg]
            )


class TestClustering:
    def test_synonymous_units_cluster_together(self) -> None:
        a = _unit(unit_id="ku-a", content="always write tests for new code")
        b = _unit(unit_id="ku-b", content="always write tests for old code")
        clusters = cluster_units([a, b])
        assert len(clusters) == 1
        assert set(clusters[0].unit_ids) == {"ku-a", "ku-b"}
        # Original content is preserved verbatim (no merging).
        assert a.content.startswith("always write tests")
        assert b.content.startswith("always write tests")

    def test_different_kinds_do_not_cluster(self) -> None:
        a = _unit(unit_id="ku-a", kind=UnitKind.PRINCIPLE, content="write tests")
        b = _unit(
            unit_id="ku-b",
            kind=UnitKind.TECHNIQUE,
            content="write tests write tests",
        )
        clusters = cluster_units([a, b])
        assert len(clusters) == 2  # different kind -> separate clusters

    def test_singleton_cluster_for_unique_unit(self) -> None:
        a = _unit(unit_id="ku-a", content="completely unique obscure phrasing")
        clusters = cluster_units([a])
        assert len(clusters) == 1
        assert clusters[0].unit_ids == ["ku-a"]


class TestConflictDetection:
    def test_principle_and_anti_pattern_flagged_open(self) -> None:
        principle = _unit(
            unit_id="ku-p",
            kind=UnitKind.PRINCIPLE,
            content="always write tests for new code",
        )
        anti = _unit(
            unit_id="ku-a",
            kind=UnitKind.ANTI_PATTERN,
            content="never write tests for new code",
        )
        conflicts = detect_conflicts([principle, anti])
        assert len(conflicts) == 1
        assert conflicts[0].status == "open"
        assert set(conflicts[0].unit_ids) == {"ku-p", "ku-a"}

    def test_negation_pair_flagged(self) -> None:
        a = _unit(
            unit_id="ku-1",
            kind=UnitKind.PRINCIPLE,
            content="you must not skip the linter before merge",
        )
        b = _unit(
            unit_id="ku-2",
            kind=UnitKind.PRINCIPLE,
            content="you must run the linter before merge",
        )
        conflicts = detect_conflicts([a, b])
        assert len(conflicts) == 1
        assert conflicts[0].status == "open"

    def test_no_conflict_for_unrelated_units(self) -> None:
        a = _unit(
            unit_id="ku-1",
            kind=UnitKind.TERM,
            content="a glossary definition of something",
        )
        b = _unit(
            unit_id="ku-2",
            kind=UnitKind.TERM,
            content="another entirely unrelated glossary entry",
        )
        assert detect_conflicts([a, b]) == []

    def test_conflicts_never_auto_resolved(self) -> None:
        principle = _unit(
            unit_id="ku-p",
            kind=UnitKind.PRINCIPLE,
            content="always write tests for new code",
        )
        anti = _unit(
            unit_id="ku-a",
            kind=UnitKind.ANTI_PATTERN,
            content="never write tests for new code",
        )
        for c in detect_conflicts([principle, anti]):
            assert c.status == "open"

    def test_conflict_scan_keeps_relevant_pair_with_many_unrelated_units(self) -> None:
        principle = _unit(
            unit_id="ku-principle",
            kind=UnitKind.PRINCIPLE,
            content="always validate source data before publishing",
        )
        anti = _unit(
            unit_id="ku-anti",
            kind=UnitKind.ANTI_PATTERN,
            content="never validate source data before publishing",
        )
        unrelated = [
            _unit(
                unit_id=f"ku-term-{index}",
                kind=UnitKind.TERM,
                content=f"unrelated glossary token {index}",
            )
            for index in range(100)
        ]

        conflicts = detect_conflicts([*unrelated, principle, anti])

        assert len(conflicts) == 1
        assert set(conflicts[0].unit_ids) == {"ku-principle", "ku-anti"}


class TestVersioning:
    def test_latest_record_picks_highest_version(self) -> None:
        v1 = _unit(unit_id="ku-1", record_version=1, content="first")
        v2 = _unit(unit_id="ku-1", record_version=2, content="second")
        assert latest_record([v1, v2], "ku-1").content == "second"

    def test_latest_record_missing_returns_none(self) -> None:
        assert latest_record([_unit()], "nope") is None

    def test_build_supersession_bumps_version_and_links(self) -> None:
        old = _unit(unit_id="ku-1", record_version=1, content="first")
        new = _unit(
            unit_id="ku-temp",
            record_version=1,
            content="corrected",
            review_status=KnowledgeStatus.APPROVED,
        )
        successor = build_supersession(old, new)
        assert successor.unit_id == "ku-1"
        assert successor.record_version == 2
        assert successor.supersedes == "ku-1"
        assert successor.content == "corrected"
        # Inputs are not mutated.
        assert old.record_version == 1
        assert new.unit_id == "ku-temp"
