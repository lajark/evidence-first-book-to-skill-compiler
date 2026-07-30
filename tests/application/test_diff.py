"""Tests for the diff engine and three-way merge (TASK-014, PRD FR-03-4 / FR-06)."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

import pytest

from book2skill.application.diff import (
    DiffEngine,
    DiffResult,
    Override,
    OverrideField,
    bundle_to_units,
    load_diff_input,
)
from book2skill.application.models import (
    AnalysisBundle,
    CandidateUnit,
    StructureEntry,
)
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.domain.knowledge import (
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    UnitKind,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now(minute_offset: int = 0) -> _dt.datetime:
    base = _dt.datetime(2026, 7, 29, 12, 0, 0, tzinfo=_dt.UTC)
    return base + _dt.timedelta(minutes=minute_offset)


def _unit(
    *,
    unit_id: str = "u1",
    kind: UnitKind = UnitKind.PRINCIPLE,
    content: str = "Always validate input before processing.",
    conditions: list[str] | None = None,
    exceptions: list[str] | None = None,
    confidence: float | None = 0.8,
    status: KnowledgeStatus = KnowledgeStatus.CANDIDATE,
    record_version: int = 1,
    source_id: str = "src1",
) -> KnowledgeUnit:
    return KnowledgeUnit(
        unit_id=unit_id,
        kind=kind,
        content=content,
        conditions=conditions or [],
        exceptions=exceptions or [],
        source_refs=[KnowledgeRef(source_id=source_id, block_id=f"{source_id}-1")],
        confidence=confidence,
        review_status=status,
        record_version=record_version,
    )


def _override(
    *,
    unit_id: str = "u1",
    field: OverrideField = OverrideField.CONTENT,
    value: Any = "Human-edited content.",
    reason: str = "Clarity improvement",
    reviewer: str = "human",
    minute_offset: int = 0,
    superseded: bool = False,
    override_id: str | None = None,
) -> Override:
    return Override(
        override_id=override_id or f"ov-{unit_id}-{field.value}",
        unit_id=unit_id,
        field=field,
        value=value,
        reason=reason,
        reviewer=reviewer,
        created_at=_now(minute_offset),
        superseded=superseded,
    )


# ---------------------------------------------------------------------------
# DiffEngine.diff
# ---------------------------------------------------------------------------


class TestDiffEngineDiff:
    """Tests for the pure diff() method."""

    def test_empty_collections_yield_empty_result(self) -> None:
        result = DiffEngine().diff([], [])
        assert isinstance(result, DiffResult)
        assert result.added == []
        assert result.removed == []
        assert result.modified == []
        assert result.conflicts == []
        assert result.unchanged == []
        assert not result.has_changes

    def test_new_only_units_are_added(self) -> None:
        u = _unit(unit_id="u1")
        result = DiffEngine().diff([], [u])
        assert result.added == [u]
        assert result.removed == []
        assert result.has_changes

    def test_old_only_units_are_removed(self) -> None:
        u = _unit(unit_id="u1")
        result = DiffEngine().diff([u], [])
        assert result.removed == [u]
        assert result.added == []

    def test_identical_units_are_unchanged(self) -> None:
        u = _unit(unit_id="u1")
        result = DiffEngine().diff([u], [u])
        assert result.unchanged == [u]
        assert result.modified == []
        assert not result.has_changes

    def test_content_change_is_modified(self) -> None:
        old = _unit(unit_id="u1", content="Old content here.")
        new = _unit(unit_id="u1", content="New content here.")
        result = DiffEngine().diff([old], [new])
        assert len(result.modified) == 1
        change = result.modified[0]
        assert change.unit_id == "u1"
        assert "content" in change.changed_fields
        assert change.old == old
        assert change.new == new

    def test_kind_change_is_modified(self) -> None:
        old = _unit(unit_id="u1", kind=UnitKind.PRINCIPLE)
        new = _unit(unit_id="u1", kind=UnitKind.TECHNIQUE)
        result = DiffEngine().diff([old], [new])
        assert len(result.modified) == 1
        assert "kind" in result.modified[0].changed_fields

    def test_conditions_change_is_modified(self) -> None:
        old = _unit(unit_id="u1", conditions=["when x"])
        new = _unit(unit_id="u1", conditions=["when x", "when y"])
        result = DiffEngine().diff([old], [new])
        assert len(result.modified) == 1
        assert "conditions" in result.modified[0].changed_fields

    def test_confidence_unchanged_within_tolerance(self) -> None:
        old = _unit(unit_id="u1", confidence=0.8)
        new = _unit(unit_id="u1", confidence=0.8 + 1e-12)
        result = DiffEngine().diff([old], [new])
        assert result.modified == []
        assert len(result.unchanged) == 1

    def test_confidence_none_vs_value_is_modified(self) -> None:
        old = _unit(unit_id="u1", confidence=None)
        new = _unit(unit_id="u1", confidence=0.5)
        result = DiffEngine().diff([old], [new])
        assert len(result.modified) == 1

    def test_mixed_scenario(self) -> None:
        old = [
            _unit(unit_id="u1", content="old"),
            _unit(unit_id="u2", content="unchanged"),
            _unit(unit_id="u3", content="will be removed"),
        ]
        new = [
            _unit(unit_id="u1", content="new"),
            _unit(unit_id="u2", content="unchanged"),
            _unit(unit_id="u4", content="added"),
        ]
        result = DiffEngine().diff(old, new)
        assert {u.unit_id for u in result.added} == {"u4"}
        assert {u.unit_id for u in result.removed} == {"u3"}
        assert {c.unit_id for c in result.modified} == {"u1"}
        assert {u.unit_id for u in result.unchanged} == {"u2"}

    def test_superseded_history_uses_latest_record(self) -> None:
        """When old contains superseded records, diff uses the latest version."""
        old_v1 = _unit(unit_id="u1", content="v1", record_version=1)
        old_v2 = _unit(unit_id="u1", content="v2", record_version=2)
        new = _unit(unit_id="u1", content="v2", record_version=1)
        result = DiffEngine().diff([old_v1, old_v2], [new])
        # old's latest is v2, new is v2 (same content) → unchanged.
        assert result.modified == []
        assert len(result.unchanged) == 1
        assert result.unchanged[0].content == "v2"


# ---------------------------------------------------------------------------
# DiffEngine.merge_with_overrides
# ---------------------------------------------------------------------------


class TestMergeWithOverrides:
    """Tests for the three-way merge."""

    def test_no_overrides_adopts_new(self) -> None:
        base = _unit(unit_id="u1", content="base")
        new = _unit(unit_id="u1", content="new")
        result = DiffEngine().merge_with_overrides([base], [new], [])
        assert len(result.merged) == 1
        assert result.merged[0].content == "new"
        assert result.applied_overrides == []
        assert result.new_conflicts == []

    def test_override_only_keeps_ours(self) -> None:
        """Human override on content, generator did not change content."""
        base = _unit(unit_id="u1", content="base")
        new = _unit(unit_id="u1", content="base")  # generator unchanged
        ov = _override(value="human-edited")
        result = DiffEngine().merge_with_overrides([base], [new], [ov])
        assert result.merged[0].content == "human-edited"
        assert ov in result.applied_overrides
        assert result.new_conflicts == []

    def test_generator_only_adopts_theirs(self) -> None:
        """No override on content, generator changed content."""
        base = _unit(unit_id="u1", content="base")
        new = _unit(unit_id="u1", content="generator-new")
        result = DiffEngine().merge_with_overrides([base], [new], [])
        assert result.merged[0].content == "generator-new"

    def test_both_changed_same_field_is_conflict(self) -> None:
        """Both human and generator changed content differently → conflict."""
        base = _unit(unit_id="u1", content="base")
        new = _unit(unit_id="u1", content="generator-new")
        ov = _override(value="human-new")
        result = DiffEngine().merge_with_overrides([base], [new], [ov])
        # Human wins as canonical.
        assert result.merged[0].content == "human-new"
        assert len(result.new_conflicts) == 1
        conflict = result.new_conflicts[0]
        assert conflict.unit_id == "u1"
        assert conflict.field == "content"
        assert conflict.human_value == "human-new"
        assert conflict.generator_value == "generator-new"
        assert ov in result.applied_overrides

    def test_different_fields_no_conflict(self) -> None:
        """Human overrides conditions, generator changes content → both applied."""
        base = _unit(unit_id="u1", content="base", conditions=["a"])
        new = _unit(unit_id="u1", content="generator-new", conditions=["a"])
        ov = _override(
            field=OverrideField.CONDITIONS, value=["a", "b"]
        )
        result = DiffEngine().merge_with_overrides([base], [new], [ov])
        assert result.merged[0].content == "generator-new"
        assert result.merged[0].conditions == ["a", "b"]
        assert result.new_conflicts == []

    def test_human_and_generator_agree_no_conflict(self) -> None:
        """Human and generator made the same change → no conflict."""
        base = _unit(unit_id="u1", content="base")
        new = _unit(unit_id="u1", content="agreed")
        ov = _override(value="agreed")
        result = DiffEngine().merge_with_overrides([base], [new], [ov])
        assert result.merged[0].content == "agreed"
        assert result.new_conflicts == []

    def test_superseded_override_not_applied(self) -> None:
        base = _unit(unit_id="u1", content="base")
        new = _unit(unit_id="u1", content="generator-new")
        ov = _override(value="old-human", superseded=True)
        result = DiffEngine().merge_with_overrides([base], [new], [ov])
        # Superseded override ignored → adopt generator.
        assert result.merged[0].content == "generator-new"
        assert ov not in result.applied_overrides

    def test_added_unit_adopted_override_preserved(self) -> None:
        """Unit only in new (added) → adopted as-is; override preserved."""
        new = _unit(unit_id="u1", content="new-unit")
        ov = _override(unit_id="u1", value="human-edit")
        result = DiffEngine().merge_with_overrides([], [new], [ov])
        assert len(result.merged) == 1
        assert result.merged[0].content == "new-unit"
        # Override for added unit has no base to apply against → preserved.
        assert ov in result.preserved_overrides

    def test_removed_unit_override_preserved(self) -> None:
        """Unit only in base (removed) → dropped; override preserved."""
        base = _unit(unit_id="u1", content="base")
        ov = _override(unit_id="u1", value="human-edit")
        result = DiffEngine().merge_with_overrides([base], [], [ov])
        assert result.merged == []
        assert ov in result.preserved_overrides

    def test_empty_inputs_yield_empty_merge(self) -> None:
        result = DiffEngine().merge_with_overrides([], [], [])
        assert result.merged == []
        assert result.applied_overrides == []

    def test_multiple_overrides_same_field_latest_wins(self) -> None:
        """Two overrides on same (unit, field): latest created_at wins."""
        base = _unit(unit_id="u1", content="base")
        new = _unit(unit_id="u1", content="base")  # generator unchanged
        ov_old = _override(value="old-edit", minute_offset=0, override_id="ov-1")
        ov_new = _override(value="new-edit", minute_offset=10, override_id="ov-2")
        result = DiffEngine().merge_with_overrides([base], [new], [ov_old, ov_new])
        assert result.merged[0].content == "new-edit"

    def test_confidence_override(self) -> None:
        base = _unit(unit_id="u1", confidence=0.5)
        new = _unit(unit_id="u1", confidence=0.5)  # generator unchanged
        ov = _override(field=OverrideField.CONFIDENCE, value=0.95)
        result = DiffEngine().merge_with_overrides([base], [new], [ov])
        assert result.merged[0].confidence == 0.95

    def test_review_status_override(self) -> None:
        base = _unit(unit_id="u1", status=KnowledgeStatus.CANDIDATE)
        new = _unit(unit_id="u1", status=KnowledgeStatus.CANDIDATE)
        ov = _override(field=OverrideField.REVIEW_STATUS, value="approved")
        result = DiffEngine().merge_with_overrides([base], [new], [ov])
        assert result.merged[0].review_status == "approved"


# ---------------------------------------------------------------------------
# Override model validation
# ---------------------------------------------------------------------------


class TestOverrideValidation:
    def test_content_must_be_nonempty_str(self) -> None:
        ov = _override(value="")
        with pytest.raises(ValueError, match="non-empty str"):
            ov.validate_value()

    def test_conditions_must_be_list_of_str(self) -> None:
        ov = _override(field=OverrideField.CONDITIONS, value="not-a-list")
        with pytest.raises(ValueError, match="list"):
            ov.validate_value()

    def test_confidence_must_be_in_range(self) -> None:
        ov = _override(field=OverrideField.CONFIDENCE, value=1.5)
        with pytest.raises(ValueError, match="float"):
            ov.validate_value()

    def test_review_status_must_be_valid(self) -> None:
        ov = _override(field=OverrideField.REVIEW_STATUS, value="bogus")
        with pytest.raises(ValueError, match="review_status"):
            ov.validate_value()

    def test_valid_content_override_passes(self) -> None:
        ov = _override(value="A valid content override.")
        ov.validate_value()  # no raise


# ---------------------------------------------------------------------------
# Bundle conversion
# ---------------------------------------------------------------------------


class TestBundleConversion:
    def _bundle(
        self, *, candidate_units: list[CandidateUnit] | None = None
    ) -> AnalysisBundle:
        return AnalysisBundle(
            collection_id="col-test",
            source_ids=["src1"],
            structure=[
                StructureEntry(
                    block_id="src1-1",
                    locator={"kind": "paragraph", "paragraph": 1},
                    heading="Intro",
                    level=1,
                    text_preview="Intro",
                )
            ],
            candidate_units=candidate_units
            or [
                CandidateUnit(
                    unit_id="cu-1",
                    kind="principle",
                    content="A principle.",
                    source_refs=[{"source_id": "src1", "block_id": "src1-1"}],
                    confidence=0.8,
                    review_status="candidate",
                    record_version=1,
                )
            ],
            review_queue=[],
        )

    def test_bundle_to_units_basic(self) -> None:
        bundle = self._bundle()
        units = bundle_to_units(bundle)
        assert len(units) == 1
        assert units[0].unit_id == "cu-1"
        assert units[0].kind == UnitKind.PRINCIPLE
        assert units[0].content == "A principle."

    def test_bundle_to_units_unknown_kind_falls_back(self) -> None:
        bundle = self._bundle(
            candidate_units=[
                CandidateUnit(
                    unit_id="cu-x",
                    kind="mystery_kind",
                    content="Content.",
                    source_refs=[{"source_id": "src1", "block_id": "src1-1"}],
                    confidence=0.5,
                    review_status="candidate",
                    record_version=1,
                )
            ]
        )
        units = bundle_to_units(bundle)
        assert units[0].kind == UnitKind.TECHNIQUE  # fallback

    def test_load_diff_input_from_bundle_file(self, tmp_path: Path) -> None:
        bundle = self._bundle()
        path = tmp_path / "bundle.json"
        path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        units = load_diff_input(str(path))
        assert len(units) == 1
        assert units[0].unit_id == "cu-1"

    def test_load_diff_input_invalid_json_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid", encoding="utf-8")
        with pytest.raises(DomainError) as exc:
            load_diff_input(str(bad))
        assert exc.value.code == ErrorCode.DIFF_INPUT_INVALID

    def test_load_diff_input_empty_candidates_raises(self, tmp_path: Path) -> None:
        # Construct an empty-candidates bundle directly (bypassing the helper's
        # `or` default which would substitute a non-empty list).
        bundle = AnalysisBundle(
            collection_id="col-empty",
            source_ids=["src1"],
            structure=[
                StructureEntry(
                    block_id="src1-1",
                    locator={"kind": "paragraph", "paragraph": 1},
                    heading="Intro",
                    level=1,
                    text_preview="Intro",
                )
            ],
            candidate_units=[],
            review_queue=[],
        )
        path = tmp_path / "empty.json"
        path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        with pytest.raises(DomainError) as exc:
            load_diff_input(str(path))
        assert exc.value.code == ErrorCode.DIFF_INPUT_INVALID
        assert "no candidate_units" in exc.value.message

    def test_load_diff_input_collection_without_storage_raises(self) -> None:
        with pytest.raises(DomainError) as exc:
            load_diff_input("some-collection-id", schema_storage=None)
        assert exc.value.code == ErrorCode.DIFF_INPUT_INVALID


# ---------------------------------------------------------------------------
# CLI diff command
# ---------------------------------------------------------------------------


class TestDiffCLI:
    """CLI integration tests for the diff command."""

    def _write_bundle(self, path: Path, content: str = "A principle.") -> Path:
        bundle = AnalysisBundle(
            collection_id="col-test",
            source_ids=["src1"],
            structure=[
                StructureEntry(
                    block_id="src1-1",
                    locator={"kind": "paragraph", "paragraph": 1},
                    heading="Intro",
                    level=1,
                    text_preview="Intro",
                )
            ],
            candidate_units=[
                CandidateUnit(
                    unit_id="cu-1",
                    kind="principle",
                    content=content,
                    source_refs=[{"source_id": "src1", "block_id": "src1-1"}],
                    confidence=0.8,
                    review_status="candidate",
                    record_version=1,
                )
            ],
            review_queue=[],
        )
        path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        return path

    def test_diff_two_bundles_succeeds(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from book2skill.cli import app

        old_path = self._write_bundle(tmp_path / "old.json", "Old content.")
        new_path = self._write_bundle(tmp_path / "new.json", "New content.")
        runner = CliRunner()
        result = runner.invoke(
            app, ["diff", str(old_path), str(new_path), "--json"]
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["modified"][0]["unit_id"] == "cu-1"

    def test_diff_identical_bundles_no_changes(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from book2skill.cli import app

        old_path = self._write_bundle(tmp_path / "old.json", "Same content.")
        new_path = self._write_bundle(tmp_path / "new.json", "Same content.")
        runner = CliRunner()
        result = runner.invoke(app, ["diff", str(old_path), str(new_path)])
        assert result.exit_code == 0, result.stdout
        assert "No differences" in result.stdout or "unchanged" in result.stdout

    def test_diff_missing_file_exits_nonzero(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from book2skill.cli import app

        new_path = self._write_bundle(tmp_path / "new.json", "Content.")
        runner = CliRunner()
        result = runner.invoke(
            app, ["diff", str(tmp_path / "missing.json"), str(new_path)]
        )
        assert result.exit_code == 1
        assert "DIFF_INPUT_INVALID" in result.stdout

    def test_diff_collection_without_data_home_fails(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from book2skill.cli import app

        new_path = self._write_bundle(tmp_path / "new.json", "Content.")
        runner = CliRunner()
        result = runner.invoke(
            app, ["diff", "some-collection", str(new_path)]
        )
        assert result.exit_code == 1
        assert "DIFF_INPUT_INVALID" in result.stdout
