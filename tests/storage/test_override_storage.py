"""Tests for OverrideStorage (TASK-014)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from book2skill.application.diff import Override, OverrideField
from book2skill.storage.errors import StorageNotFoundError
from book2skill.storage.override_storage import OverrideStorage


def _now(minute: int = 0) -> _dt.datetime:
    base = _dt.datetime(2026, 7, 29, 12, 0, 0, tzinfo=_dt.UTC)
    return base + _dt.timedelta(minutes=minute)


def _override(
    *,
    override_id: str = "ov-1",
    unit_id: str = "u1",
    field: OverrideField = OverrideField.CONTENT,
    value: str = "Edited content.",
    minute: int = 0,
    superseded: bool = False,
) -> Override:
    return Override(
        override_id=override_id,
        unit_id=unit_id,
        field=field,
        value=value,
        reason="test override",
        reviewer="tester",
        created_at=_now(minute),
        superseded=superseded,
    )


class TestOverrideStorageRoundTrip:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        store = OverrideStorage(tmp_path)
        ov = _override()
        store.save_override("col-1", ov)
        loaded = store.load_overrides("col-1")
        assert len(loaded) == 1
        assert loaded[0].override_id == "ov-1"
        assert loaded[0].value == "Edited content."

    def test_multiple_saves_are_appended(self, tmp_path: Path) -> None:
        store = OverrideStorage(tmp_path)
        store.save_override("col-1", _override(override_id="ov-1", minute=0))
        store.save_override(
            "col-1",
            _override(override_id="ov-2", unit_id="u2", minute=5),
        )
        loaded = store.load_overrides("col-1")
        assert len(loaded) == 2
        assert {o.override_id for o in loaded} == {"ov-1", "ov-2"}

    def test_empty_collection_returns_empty_list(self, tmp_path: Path) -> None:
        store = OverrideStorage(tmp_path)
        assert store.load_overrides("missing-col") == []


class TestActiveOverrides:
    def test_load_active_deduplicates_by_unit_and_field(self, tmp_path: Path) -> None:
        """Same (unit, field) with two records → latest created_at wins."""
        store = OverrideStorage(tmp_path)
        store.save_override(
            "col-1", _override(override_id="ov-1", value="old", minute=0)
        )
        store.save_override(
            "col-1", _override(override_id="ov-2", value="new", minute=10)
        )
        active = store.load_active_overrides("col-1")
        assert len(active) == 1
        assert active[0].value == "new"

    def test_load_active_skips_superseded(self, tmp_path: Path) -> None:
        store = OverrideStorage(tmp_path)
        store.save_override(
            "col-1", _override(override_id="ov-1", value="v1", superseded=True)
        )
        store.save_override(
            "col-1",
            _override(
                override_id="ov-2",
                field=OverrideField.CONFIDENCE,
                value=0.9,
                minute=5,
            ),
        )
        active = store.load_active_overrides("col-1")
        # Only the non-superseded override is active.
        assert len(active) == 1
        assert active[0].field == OverrideField.CONFIDENCE

    def test_different_fields_keep_separate(self, tmp_path: Path) -> None:
        store = OverrideStorage(tmp_path)
        store.save_override(
            "col-1", _override(override_id="ov-1", field=OverrideField.CONTENT)
        )
        store.save_override(
            "col-1",
            _override(
                override_id="ov-2", field=OverrideField.CONFIDENCE, value=0.9
            ),
        )
        active = store.load_active_overrides("col-1")
        assert len(active) == 2

    def test_load_for_unit_filters(self, tmp_path: Path) -> None:
        store = OverrideStorage(tmp_path)
        store.save_override("col-1", _override(unit_id="u1"))
        store.save_override(
            "col-1", _override(override_id="ov-2", unit_id="u2")
        )
        u1_overrides = store.load_for_unit("col-1", "u1")
        assert len(u1_overrides) == 1
        assert u1_overrides[0].unit_id == "u1"


class TestSupersedeOverride:
    def test_supersede_marks_record(self, tmp_path: Path) -> None:
        store = OverrideStorage(tmp_path)
        store.save_override("col-1", _override(override_id="ov-1"))
        store.supersede_override("col-1", "ov-1")
        active = store.load_active_overrides("col-1")
        assert active == []

    def test_supersede_missing_raises(self, tmp_path: Path) -> None:
        store = OverrideStorage(tmp_path)
        with pytest.raises(StorageNotFoundError):
            store.supersede_override("col-1", "nonexistent")


class TestValidation:
    def test_save_invalid_value_raises(self, tmp_path: Path) -> None:
        """Bad override value is rejected before reaching disk."""
        store = OverrideStorage(tmp_path)
        bad = Override(
            override_id="ov-bad",
            unit_id="u1",
            field=OverrideField.CONTENT,
            value="",  # empty content not allowed
            reason="test",
            reviewer="tester",
            created_at=_now(),
        )
        with pytest.raises(ValueError, match="non-empty"):
            store.save_override("col-1", bad)
        # Nothing written.
        assert store.load_overrides("col-1") == []


class TestIsolation:
    def test_collections_are_isolated(self, tmp_path: Path) -> None:
        store = OverrideStorage(tmp_path)
        store.save_override("col-a", _override(unit_id="u1"))
        store.save_override(
            "col-b", _override(override_id="ov-2", unit_id="u2")
        )
        assert len(store.load_overrides("col-a")) == 1
        assert len(store.load_overrides("col-b")) == 1
        assert store.load_overrides("col-a")[0].unit_id == "u1"
        assert store.load_overrides("col-b")[0].unit_id == "u2"
