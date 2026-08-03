"""Tests for the typed Schema-layer knowledge-unit storage."""

from __future__ import annotations

from pathlib import Path

import pytest

import book2skill.storage.schema_storage as schema_storage_module
from book2skill.domain import (
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    UnitKind,
)
from book2skill.storage import (
    KnowledgeSchemaStorage,
    StorageNotFoundError,
    StoragePathError,
)


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeSchemaStorage:
    return KnowledgeSchemaStorage(data_home=tmp_path)


def _ref(block_id: str = "blk-1") -> KnowledgeRef:
    return KnowledgeRef(source_id="src-1", block_id=block_id, quote="q")


def _unit(
    *,
    unit_id: str = "ku-1",
    kind: UnitKind = UnitKind.PRINCIPLE,
    content: str = "Always write tests.",
    record_version: int = 1,
    review_status: KnowledgeStatus = KnowledgeStatus.CANDIDATE,
) -> KnowledgeUnit:
    return KnowledgeUnit(
        unit_id=unit_id,
        kind=kind,
        content=content,
        source_refs=[_ref()],
        confidence=0.8,
        review_status=review_status,
        record_version=record_version,
    )


class TestSaveLoadUnit:
    def test_round_trip(self, store: KnowledgeSchemaStorage) -> None:
        unit = _unit(content="Always write tests.")
        path = store.save_unit("col-1", unit)
        assert path.exists()
        loaded = store.load_units("col-1")
        assert len(loaded) == 1
        assert loaded[0].unit_id == "ku-1"
        assert loaded[0].content == "Always write tests."

    def test_multiple_saves_accumulate(
        self, store: KnowledgeSchemaStorage
    ) -> None:
        store.save_unit("col-1", _unit(unit_id="ku-1"))
        store.save_unit("col-1", _unit(unit_id="ku-2"))
        store.save_unit("col-1", _unit(unit_id="ku-3"))
        loaded = store.load_units("col-1")
        assert [u.unit_id for u in loaded] == ["ku-1", "ku-2", "ku-3"]

    def test_load_empty_collection(self, store: KnowledgeSchemaStorage) -> None:
        assert store.load_units("absent") == []

    def test_load_unit_missing_returns_none(
        self, store: KnowledgeSchemaStorage
    ) -> None:
        store.save_unit("col-1", _unit(unit_id="ku-1"))
        assert store.load_unit("col-1", "nope") is None


class TestSaveUnitsAtomic:
    def test_preserves_order_across_single_and_batch_saves(
        self, store: KnowledgeSchemaStorage
    ) -> None:
        store.save_unit("col-1", _unit(unit_id="ku-1"))

        path = store.save_units_atomic(
            "col-1",
            [_unit(unit_id="ku-2"), _unit(unit_id="ku-3")],
        )
        store.save_unit("col-1", _unit(unit_id="ku-4"))

        assert path.exists()
        assert [unit.unit_id for unit in store.load_units("col-1")] == [
            "ku-1",
            "ku-2",
            "ku-3",
            "ku-4",
        ]

    def test_save_new_units_atomic_skips_existing_history(
        self, store: KnowledgeSchemaStorage
    ) -> None:
        original = _unit(unit_id="ku-1")
        store.save_unit("col-1", original)

        store.save_new_units_atomic("col-1", [original, _unit(unit_id="ku-2")])

        assert [unit.unit_id for unit in store.load_units("col-1")] == [
            "ku-1",
            "ku-2",
        ]

    def test_builds_complete_jsonl_for_one_atomic_write(
        self,
        store: KnowledgeSchemaStorage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store.save_unit("col-1", _unit(unit_id="ku-1"))
        calls: list[tuple[Path, str | bytes]] = []

        def capture_atomic_write(path: Path, content: str | bytes) -> None:
            calls.append((path, content))

        monkeypatch.setattr(
            schema_storage_module,
            "atomic_write",
            capture_atomic_write,
        )

        store.save_units_atomic(
            "col-1",
            [_unit(unit_id="ku-2"), _unit(unit_id="ku-3")],
        )

        assert len(calls) == 1
        content = calls[0][1]
        assert isinstance(content, str)
        assert [
            KnowledgeUnit.model_validate_json(line).unit_id
            for line in content.splitlines()
        ] == ["ku-1", "ku-2", "ku-3"]

    def test_atomic_write_failure_keeps_existing_file_unchanged(
        self,
        store: KnowledgeSchemaStorage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = store.save_unit("col-1", _unit(unit_id="ku-1"))
        before = path.read_bytes()

        def fail_atomic_write(_path: Path, _content: str | bytes) -> None:
            raise OSError("simulated atomic replace failure")

        monkeypatch.setattr(
            schema_storage_module,
            "atomic_write",
            fail_atomic_write,
        )

        with pytest.raises(OSError, match="simulated atomic replace failure"):
            store.save_units_atomic(
                "col-1",
                [_unit(unit_id="ku-2"), _unit(unit_id="ku-3")],
            )

        assert path.read_bytes() == before
        assert [unit.unit_id for unit in store.load_units("col-1")] == ["ku-1"]

    def test_empty_batch_does_not_create_or_touch_file(
        self,
        store: KnowledgeSchemaStorage,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        existing_path = store.save_unit("col-1", _unit(unit_id="ku-1"))
        before = existing_path.read_bytes()
        expected = tmp_path / "schema" / "empty" / "units.jsonl"

        def fail_if_called(_path: Path, _content: str | bytes) -> None:
            raise AssertionError("atomic_write must not run for an empty batch")

        monkeypatch.setattr(
            schema_storage_module,
            "atomic_write",
            fail_if_called,
        )

        assert store.save_units_atomic("col-1", []) == existing_path
        path = store.save_units_atomic("empty", [])

        assert existing_path.read_bytes() == before
        assert path == expected
        assert not path.exists()
        assert not path.parent.exists()


class TestVersioning:
    def test_load_unit_returns_latest(self, store: KnowledgeSchemaStorage) -> None:
        store.save_unit("col-1", _unit(unit_id="ku-1", record_version=1))
        store.save_unit(
            "col-1",
            _unit(
                unit_id="ku-1",
                record_version=2,
                content="corrected",
            ),
        )
        latest = store.load_unit("col-1", "ku-1")
        assert latest is not None
        assert latest.record_version == 2
        assert latest.content == "corrected"

    def test_load_unit_history_ordered(
        self, store: KnowledgeSchemaStorage
    ) -> None:
        store.save_unit("col-1", _unit(unit_id="ku-1", record_version=1))
        store.save_unit("col-1", _unit(unit_id="ku-1", record_version=3))
        store.save_unit("col-1", _unit(unit_id="ku-1", record_version=2))
        history = store.load_unit_history("col-1", "ku-1")
        assert [h.record_version for h in history] == [1, 2, 3]

    def test_supersede_appends_without_erasing(
        self, store: KnowledgeSchemaStorage
    ) -> None:
        old = _unit(unit_id="ku-1", record_version=1, content="first")
        store.save_unit("col-1", old)
        successor = store.supersede_unit(
            "col-1",
            "ku-1",
            _unit(
                unit_id="ku-tmp",
                record_version=1,
                content="second",
                review_status=KnowledgeStatus.APPROVED,
            ),
        )
        assert successor.unit_id == "ku-1"
        assert successor.record_version == 2
        assert successor.supersedes == "ku-1"
        # History preserved: both the original and the successor exist.
        history = store.load_unit_history("col-1", "ku-1")
        assert len(history) == 2
        assert history[0].content == "first"
        assert history[1].content == "second"
        assert store.load_unit("col-1", "ku-1").content == "second"

    def test_supersede_unknown_unit_raises(
        self, store: KnowledgeSchemaStorage
    ) -> None:
        with pytest.raises(StorageNotFoundError):
            store.supersede_unit("col-1", "nope", _unit(unit_id="x"))


class TestLoadByKind:
    def test_filters_by_kind(self, store: KnowledgeSchemaStorage) -> None:
        store.save_unit(
            "col-1", _unit(unit_id="ku-1", kind=UnitKind.FRAMEWORK)
        )
        store.save_unit(
            "col-1", _unit(unit_id="ku-2", kind=UnitKind.PRINCIPLE)
        )
        store.save_unit(
            "col-1", _unit(unit_id="ku-3", kind=UnitKind.FRAMEWORK)
        )
        frameworks = store.load_by_kind("col-1", UnitKind.FRAMEWORK)
        assert {f.unit_id for f in frameworks} == {"ku-1", "ku-3"}

    def test_returns_latest_per_unit(self, store: KnowledgeSchemaStorage) -> None:
        store.save_unit(
            "col-1",
            _unit(unit_id="ku-1", kind=UnitKind.FRAMEWORK, record_version=1),
        )
        store.save_unit(
            "col-1",
            _unit(
                unit_id="ku-1",
                kind=UnitKind.FRAMEWORK,
                record_version=2,
                content="updated",
            ),
        )
        frameworks = store.load_by_kind("col-1", UnitKind.FRAMEWORK)
        assert len(frameworks) == 1
        assert frameworks[0].record_version == 2


class TestCollectionMetaAndSafety:
    def test_collection_metadata_round_trip(
        self, store: KnowledgeSchemaStorage
    ) -> None:
        store.save_collection("col-1", {"name": "My Collection", "n": 3})
        loaded = store.load_collection("col-1")
        assert loaded["name"] == "My Collection"
        assert loaded["n"] == 3

    def test_path_traversal_blocked(self, store: KnowledgeSchemaStorage) -> None:
        with pytest.raises(StoragePathError):
            store.save_unit("../escape", _unit(unit_id="ku-1"))
