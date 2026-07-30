"""Tests for the Publisher (TASK-015, PRD FR-03-4 / FR-10).

Covers atomic publish, snapshot, rollback, index.md and the append-only
publish-log. Stays hermetic: tmp_path data_home, an incrementing fake clock to
keep snapshot directory names unique and deterministic, and units/spec built
in-memory (no extractor or LLM).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from book2skill.application.publisher import (
    Publisher,
    load_skill_meta,
)
from book2skill.compiler import SkillSpec
from book2skill.compiler.token_budget import TokenBudget
from book2skill.domain import KnowledgeStatus, KnowledgeUnit, PublishStatus
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.storage.errors import StorageNotFoundError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(
    *,
    name: str = "test-skill",
    description: str = "A test skill that is fully described.",
) -> SkillSpec:
    return SkillSpec(
        name=name,
        description=description,
        use_when=["When you need a test skill."],
        do_not_use_when=["When you do not need it."],
    )


def _unit(
    unit_id: str,
    content: str = "Always validate input before processing it further.",
    *,
    kind: str = "principle",
    review_status: KnowledgeStatus = KnowledgeStatus.APPROVED,
) -> KnowledgeUnit:
    return KnowledgeUnit(
        unit_id=unit_id,
        kind=kind,
        content=content,
        source_refs=[{"source_id": "src1", "block_id": "b1"}],
        confidence=0.8,
        review_status=review_status,
        record_version=1,
    )


class _Clock:
    """Deterministic, monotonically increasing UTC clock for snapshots."""

    def __init__(self, start: dt.datetime | None = None) -> None:
        self._t = start or dt.datetime(2026, 7, 29, 12, 0, 0, tzinfo=dt.UTC)

    def __call__(self) -> dt.datetime:
        result = self._t
        self._t = self._t + dt.timedelta(seconds=1)
        return result


def _publisher(
    tmp_path: Path, *, budget: TokenBudget | None = None
) -> Publisher:
    return Publisher(tmp_path / "data", now=_Clock(), budget=budget)


# ---------------------------------------------------------------------------
# publish()
# ---------------------------------------------------------------------------


class TestPublish:
    def test_first_publish_creates_skill_dir_no_snapshot(self, tmp_path: Path) -> None:
        pub = _publisher(tmp_path)
        skill_dir = tmp_path / "data" / "skills" / "test-skill"

        record = pub.publish(
            skill_dir, [_unit("u-1"), _unit("u-2", kind="technique")],
            _spec(), collection_id="col-abc",
        )

        assert record.action == "publish"
        assert record.skill_dir == skill_dir
        assert record.snapshot_path is None  # first publish, nothing to snapshot
        assert (skill_dir / "SKILL.md").exists()
        assert (skill_dir / "skill.meta.json").exists()

    def test_second_publish_snapshots_old_tree(self, tmp_path: Path) -> None:
        pub = _publisher(tmp_path)
        skill_dir = tmp_path / "data" / "skills" / "test-skill"
        pub.publish(skill_dir, [_unit("u-1")], _spec(), collection_id="col-abc")
        old_skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        record = pub.publish(
            skill_dir, [_unit("u-1", content="Revised validated input rule.")],
            _spec(), collection_id="col-abc",
        )

        assert record.snapshot_path is not None
        assert record.snapshot_path.exists()  # old tree preserved as snapshot
        # Snapshot holds the previous SKILL.md content.
        assert (
            record.snapshot_path / "SKILL.md"
        ).read_text(encoding="utf-8") == old_skill_md
        # Staging dir is gone after a successful swap.
        assert not (tmp_path / "data" / ".staging").exists() or not list(
            (tmp_path / "data" / ".staging").glob("*")
        )

    def test_skill_meta_written_with_collection_and_spec(self, tmp_path: Path) -> None:
        pub = _publisher(tmp_path)
        skill_dir = tmp_path / "data" / "skills" / "test-skill"
        spec = _spec(name="my-skill")
        pub.publish(skill_dir, [_unit("u-1")], spec, collection_id="col-xyz")

        meta = load_skill_meta(skill_dir)
        assert meta is not None
        assert meta.collection_id == "col-xyz"
        assert meta.skill_name == "my-skill"
        assert meta.spec == spec
        assert meta.publish_status == PublishStatus.PUBLISHED
        assert meta.last_published_at is not None

    def test_built_at_preserved_across_republish(self, tmp_path: Path) -> None:
        pub = _publisher(tmp_path)
        skill_dir = tmp_path / "data" / "skills" / "test-skill"
        first = pub.publish(skill_dir, [_unit("u-1")], _spec(), collection_id="col")
        meta1 = load_skill_meta(skill_dir)
        assert meta1 is not None and meta1.built_at == first.published_at

        second = pub.publish(
            skill_dir, [_unit("u-1", content="changed content here")], _spec(),
            collection_id="col",
        )
        meta2 = load_skill_meta(skill_dir)
        assert meta2 is not None
        assert meta2.built_at == meta1.built_at  # preserved
        assert meta2.last_published_at == second.published_at

    def test_budget_failure_leaves_published_tree_untouched(
        self, tmp_path: Path
    ) -> None:
        pub = _publisher(
            tmp_path, budget=TokenBudget(target_min=0, target_max=1, hard_max=1)
        )
        skill_dir = tmp_path / "data" / "skills" / "test-skill"
        # Establish a published tree first with a normal publisher.
        _publisher(tmp_path).publish(
            skill_dir, [_unit("u-1")], _spec(), collection_id="col"
        )
        old_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        with pytest.raises(DomainError) as exc_info:
            pub.publish(
                skill_dir, [_unit("u-1", content="x")], _spec(), collection_id="col"
            )
        assert exc_info.value.code == ErrorCode.BUILD_BUDGET_EXCEEDED
        # Published tree unchanged; no snapshot created by the failed run.
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == old_md

    def test_swap_failure_restores_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pub = _publisher(tmp_path)
        skill_dir = tmp_path / "data" / "skills" / "test-skill"
        pub.publish(skill_dir, [_unit("u-1")], _spec(), collection_id="col")
        old_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        # Sabotage only the swap step (staging -> skill_dir). The snapshot move
        # happens first via the real os.replace, so the old tree is already
        # aside when the swap fails → the except path must restore it.
        def boom(staging: Path, target: Path) -> None:
            raise OSError("simulated swap failure")

        monkeypatch.setattr(pub, "_swap", boom)

        with pytest.raises(DomainError) as exc_info:
            pub.publish(
                skill_dir, [_unit("u-1", content="new content for the rule")],
                _spec(), collection_id="col",
            )
        assert exc_info.value.code == ErrorCode.PUBLISH_FAILED
        # Old tree restored.
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == old_md

    def test_publish_log_and_index_updated(self, tmp_path: Path) -> None:
        pub = _publisher(tmp_path)
        skill_dir = tmp_path / "data" / "skills" / "test-skill"
        pub.publish(skill_dir, [_unit("u-1")], _spec(), collection_id="col")
        pub.publish(
            skill_dir, [_unit("u-1", content="revised content here")], _spec(),
            collection_id="col",
        )

        log_path = tmp_path / "data" / "wiki" / "publish-log.jsonl"
        lines = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 2
        assert all(e["action"] == "publish" for e in lines)
        assert lines[0]["success"] and lines[1]["success"]

        index = (tmp_path / "data" / "wiki" / "index.md").read_text(encoding="utf-8")
        assert "test-skill" in index
        assert "Published Skills" in index


# ---------------------------------------------------------------------------
# rollback_latest()
# ---------------------------------------------------------------------------


class TestRollback:
    def test_no_snapshot_raises(self, tmp_path: Path) -> None:
        pub = _publisher(tmp_path)
        skill_dir = tmp_path / "data" / "skills" / "test-skill"
        with pytest.raises(StorageNotFoundError):
            pub.rollback_latest(skill_dir)

    def test_rollback_restores_previous_version(self, tmp_path: Path) -> None:
        pub = _publisher(tmp_path)
        skill_dir = tmp_path / "data" / "skills" / "test-skill"
        pub.publish(
            skill_dir, [_unit("u-1", content="version one content")], _spec(),
            collection_id="col",
        )
        v1_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        pub.publish(
            skill_dir, [_unit("u-1", content="version two content")], _spec(),
            collection_id="col",
        )
        v2_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert v1_md != v2_md

        record = pub.rollback_latest(skill_dir)
        assert record.action == "rollback"
        # Restored to v1.
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == v1_md

        # Log carries the rollback entry.
        log = (tmp_path / "data" / "wiki" / "publish-log.jsonl").read_text("utf-8")
        assert '"rollback"' in log

    def test_rollback_is_reversible(self, tmp_path: Path) -> None:
        pub = _publisher(tmp_path)
        skill_dir = tmp_path / "data" / "skills" / "test-skill"
        pub.publish(
            skill_dir, [_unit("u-1", content="v1 content")], _spec(),
            collection_id="col",
        )
        pub.publish(
            skill_dir, [_unit("u-1", content="v2 content")], _spec(),
            collection_id="col",
        )
        v2_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        pub.rollback_latest(skill_dir)  # back to v1
        # Rolling back again restores v2 (current moved aside as a snapshot).
        pub.rollback_latest(skill_dir)
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == v2_md
