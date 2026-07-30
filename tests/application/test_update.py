"""Tests for the Update / Fold-in use case (TASK-015, PRD FR-03-4).

Covers plan (diff + merge), execute (dry-run vs confirm), override
preservation, removed-unit deprecation, idempotency, meta round-trip and the
CLI ``update`` command. Hermetic: tmp_path data_home, in-memory units/spec,
and a fake AnalyzeUseCase so the new-side bundle is deterministic.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from book2skill.application.analyze import AnalyzeResult
from book2skill.application.models import (
    AnalysisBundle,
    CandidateUnit,
    StructureEntry,
)
from book2skill.application.publisher import Publisher, load_skill_meta
from book2skill.application.update import UpdateUseCase
from book2skill.cli import app
from book2skill.compiler import SkillSpec
from book2skill.domain import (
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
)
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.storage import KnowledgeSchemaStorage, OverrideStorage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(
    *, name: str = "demo-skill",
    description: str = "A demo skill that compiles knowledge into usable form.",
) -> SkillSpec:
    return SkillSpec(
        name=name,
        description=description,
        use_when=["When you need the demo skill."],
        do_not_use_when=["When you do not need it."],
    )


def _unit(
    unit_id: str,
    content: str,
    *,
    kind: str = "principle",
    status: KnowledgeStatus = KnowledgeStatus.APPROVED,
    version: int = 1,
) -> KnowledgeUnit:
    return KnowledgeUnit(
        unit_id=unit_id,
        kind=kind,
        content=content,
        source_refs=[KnowledgeRef(source_id="src1", block_id="b1")],
        confidence=0.8,
        review_status=status,
        record_version=version,
    )


def _candidate(
    unit_id: str, content: str, *, kind: str = "principle"
) -> CandidateUnit:
    return CandidateUnit(
        unit_id=unit_id,
        kind=kind,
        content=content,
        source_refs=[{"source_id": "src1", "block_id": "b1"}],
        confidence=0.8,
        review_status="candidate",
        record_version=1,
    )


def _bundle(
    candidates: list[CandidateUnit],
    *,
    collection_id: str = "col-demo",
    source_ids: list[str] | None = None,
) -> AnalysisBundle:
    return AnalysisBundle(
        collection_id=collection_id,
        source_ids=source_ids or ["src1"],
        structure=[
            StructureEntry(
                block_id="b1",
                locator={"kind": "paragraph", "paragraph": 1},
                heading="Intro",
                level=1,
                text_preview="Intro",
            )
        ],
        candidate_units=candidates,
        review_queue=[],
    )


class _FakeAnalyze:
    """Stand-in AnalyzeUseCase that returns a fixed bundle."""

    def __init__(self, bundle: AnalysisBundle) -> None:
        self._result = AnalyzeResult(bundle=bundle, errors=[])

    def execute(
        self,
        inputs: list[str],
        *,
        collection_id: str | None = None,
        rights_note: str | None = None,
    ) -> AnalyzeResult:
        return self._result


def _seed_and_publish(
    tmp_path: Path,
    old_units: list[KnowledgeUnit],
    spec: SkillSpec,
    collection_id: str,
) -> tuple[Path, Path]:
    """Persist old units to the Schema layer and publish the v1 Skill dir.

    Returns (data_home, skill_dir) with skill.meta.json already written.
    """
    data_home = tmp_path / "data"
    schema = KnowledgeSchemaStorage(data_home)
    for u in old_units:
        schema.save_unit(collection_id, u)

    skill_dir = data_home / "skills" / spec.name
    Publisher(data_home).publish(
        skill_dir, old_units, spec, collection_id=collection_id
    )
    return data_home, skill_dir


# ---------------------------------------------------------------------------
# plan()
# ---------------------------------------------------------------------------


class TestPlan:
    def test_diff_added_modified_removed(self, tmp_path: Path) -> None:
        old = [
            _unit("u-1", "Original principle about input validation."),
            _unit("u-2", "A technique to keep.", kind="technique"),
        ]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")

        new_bundle = _bundle(
            [
                _candidate("u-1", "Revised principle about input validation."),
                _candidate("u-3", "A brand new principle.", kind="technique"),
            ]
        )
        use_case = UpdateUseCase(
            data_home, analyze_use_case=_FakeAnalyze(new_bundle)
        )
        suggestion = use_case.plan(skill_dir, ["new.txt"])

        assert {u.unit_id for u in suggestion.added} == {"u-3"}
        assert {c.unit_id for c in suggestion.modified} == {"u-1"}
        assert {u.unit_id for u in suggestion.removed} == {"u-2"}
        assert suggestion.merge is not None
        assert suggestion.has_changes

    def test_override_preserved_in_merge(self, tmp_path: Path) -> None:
        old = [_unit("u-1", "Original content for the principle rule.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")

        # Human override on u-1's content.
        ov_storage = OverrideStorage(data_home)
        from book2skill.application.diff import Override, OverrideField

        ov_storage.save_override(
            "col-demo",
            Override(
                override_id="ov-1",
                unit_id="u-1",
                field=OverrideField.CONTENT,
                value="Human-authored content for the principle rule.",
                reason="Reviewer prefers clearer wording.",
                reviewer="human",
                created_at=dt.datetime(2026, 7, 29, 12, 0, 0, tzinfo=dt.UTC),
            ),
        )

        new_bundle = _bundle(
            [_candidate("u-1", "Generator-revised content for the rule.")]
        )
        use_case = UpdateUseCase(
            data_home, analyze_use_case=_FakeAnalyze(new_bundle)
        )
        suggestion = use_case.plan(skill_dir, ["new.txt"])

        merge = suggestion.merge
        assert merge is not None
        assert any(o.unit_id == "u-1" for o in merge.applied_overrides)
        # Human value wins over the generator's conflicting value.
        merged_u1 = next(u for u in merge.merged if u.unit_id == "u-1")
        assert merged_u1.content == "Human-authored content for the principle rule."
        # Same-field double edit → recorded as an unresolvable conflict.
        assert any(c.unit_id == "u-1" for c in merge.new_conflicts)

    def test_missing_collection_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "skills" / "demo-skill"
        use_case = UpdateUseCase(tmp_path / "data")
        with pytest.raises(DomainError) as exc_info:
            use_case.plan(skill_dir, ["new.txt"])
        assert exc_info.value.code == ErrorCode.DIFF_INPUT_INVALID

    def test_empty_old_units_raises(self, tmp_path: Path) -> None:
        # No units persisted; skill_dir exists but collection empty.
        data_home = tmp_path / "data"
        skill_dir = data_home / "skills" / "demo-skill"
        # Write a meta file pointing at an empty collection.
        from book2skill.application.publisher import SkillMeta
        from book2skill.storage import atomic_write

        meta = SkillMeta(
            skill_name="demo-skill",
            collection_id="col-empty",
            spec=_spec(),
            publish_status="published",
            built_at="2026-07-29T12:00:00+00:00",
        )
        skill_dir.parent.mkdir(parents=True, exist_ok=True)
        skill_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(skill_dir / "skill.meta.json", meta.model_dump_json())
        use_case = UpdateUseCase(
            data_home,
            analyze_use_case=_FakeAnalyze(
                _bundle([_candidate("u-1", "x")], collection_id="col-empty")
            ),
        )
        with pytest.raises(DomainError) as exc_info:
            use_case.plan(skill_dir, ["new.txt"])
        assert exc_info.value.code == ErrorCode.DIFF_INPUT_INVALID


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------


class TestExecute:
    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        v1_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        new_bundle = _bundle(
            [_candidate("u-1", "Revised principle about input validation.")]
        )
        use_case = UpdateUseCase(data_home, analyze_use_case=_FakeAnalyze(new_bundle))

        result = use_case.execute(skill_dir, ["new.txt"], confirm=False)

        assert result.published is False
        assert result.reason == "dry_run"
        # Nothing changed on disk.
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == v1_md
        assert not (data_home / "snapshots").exists() or not list(
            (data_home / "snapshots").glob("*")
        )

    def test_confirm_publishes_and_snapshots(self, tmp_path: Path) -> None:
        old = [
            _unit("u-1", "Original principle about input validation."),
            _unit("u-2", "A technique to keep.", kind="technique"),
        ]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")

        new_bundle = _bundle(
            [
                _candidate("u-1", "Revised principle about input validation."),
                _candidate("u-3", "A brand new principle.", kind="technique"),
            ]
        )
        use_case = UpdateUseCase(data_home, analyze_use_case=_FakeAnalyze(new_bundle))

        result = use_case.execute(skill_dir, ["new.txt"], _spec(), confirm=True)

        assert result.published is True
        assert result.publish_record is not None
        assert result.publish_record.snapshot_path is not None
        assert result.publish_record.snapshot_path.exists()
        # New skill compiled from the merged (active) units.
        assert (skill_dir / "SKILL.md").exists()
        assert (skill_dir / "skill.meta.json").exists()

    def test_removed_units_marked_superseded(self, tmp_path: Path) -> None:
        old = [
            _unit("u-1", "Keep this principle."),
            _unit("u-2", "Drop this technique.", kind="technique"),
        ]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        schema = KnowledgeSchemaStorage(data_home)

        new_bundle = _bundle([_candidate("u-1", "Keep this principle.")])
        use_case = UpdateUseCase(data_home, analyze_use_case=_FakeAnalyze(new_bundle))
        use_case.execute(skill_dir, ["new.txt"], _spec(), confirm=True)

        history = schema.load_unit_history("col-demo", "u-2")
        assert len(history) == 2  # original + superseded
        latest = schema.load_unit("col-demo", "u-2")
        assert latest is not None
        assert str(latest.review_status) == "superseded"

    def test_modified_units_supersede_bump_version(self, tmp_path: Path) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        schema = KnowledgeSchemaStorage(data_home)

        new_bundle = _bundle(
            [_candidate("u-1", "Revised principle about input validation.")]
        )
        use_case = UpdateUseCase(data_home, analyze_use_case=_FakeAnalyze(new_bundle))
        use_case.execute(skill_dir, ["new.txt"], _spec(), confirm=True)

        history = schema.load_unit_history("col-demo", "u-1")
        assert len(history) == 2
        assert history[1].record_version == 2
        assert history[1].supersedes == "u-1"

    def test_idempotent_rerun_no_changes(self, tmp_path: Path) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")

        new_bundle = _bundle(
            [_candidate("u-1", "Revised principle about input validation.")]
        )
        use_case = UpdateUseCase(data_home, analyze_use_case=_FakeAnalyze(new_bundle))

        first = use_case.execute(skill_dir, ["new.txt"], _spec(), confirm=True)
        assert first.published is True
        log_path = data_home / "wiki" / "publish-log.jsonl"
        log_lines = log_path.read_text("utf-8").splitlines()
        snapshots_after_first = list((data_home / "snapshots" / "demo-skill").glob("*"))

        second = use_case.execute(skill_dir, ["new.txt"], _spec(), confirm=True)
        assert second.published is False
        assert second.reason == "no_changes"
        # No new log line, no new snapshot.
        assert log_path.read_text("utf-8").splitlines() == log_lines
        assert (
            list((data_home / "snapshots" / "demo-skill").glob("*"))
            == snapshots_after_first
        )

    def test_rollback_restores_previous(self, tmp_path: Path) -> None:
        old = [_unit("u-1", "v1 principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        v1_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        new_bundle = _bundle(
            [_candidate("u-1", "v2 principle about input validation.")]
        )
        use_case = UpdateUseCase(data_home, analyze_use_case=_FakeAnalyze(new_bundle))
        use_case.execute(skill_dir, ["new.txt"], _spec(), confirm=True)

        record = use_case.rollback(skill_dir)
        assert record.action == "rollback"
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == v1_md

    def test_meta_round_trip_supplies_spec_and_collection(
        self, tmp_path: Path
    ) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")

        new_bundle = _bundle(
            [_candidate("u-1", "Revised principle about input validation.")]
        )
        # No spec passed to execute — must come from skill.meta.json.
        use_case = UpdateUseCase(data_home, analyze_use_case=_FakeAnalyze(new_bundle))
        result = use_case.execute(skill_dir, ["new.txt"], confirm=True)

        assert result.published is True
        meta = load_skill_meta(skill_dir)
        assert meta is not None
        assert meta.collection_id == "col-demo"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


runner = CliRunner()


class TestUpdateCLI:
    def test_dry_run_default_no_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        v1_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        src = tmp_path / "new.txt"
        src.write_text("Revised principle about input validation.", encoding="utf-8")
        new_bundle = _bundle(
            [_candidate("u-1", "Revised principle about input validation.")]
        )

        import book2skill.cli as cli_mod

        original_init = cli_mod.UpdateUseCase.__init__

        def patched_init(self, data_home, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.setdefault("analyze_use_case", _FakeAnalyze(new_bundle))
            original_init(self, data_home, **kwargs)

        monkeypatch.setattr(cli_mod.UpdateUseCase, "__init__", patched_init)

        result = runner.invoke(
            app,
            [
                "update", str(skill_dir), str(src),
                "--data-home", str(data_home), "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["published"] is False
        assert payload["reason"] == "dry_run"
        assert payload["modified"] == 1
        # No disk changes.
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == v1_md

    def test_confirm_json_publishes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")

        src = tmp_path / "new.txt"
        src.write_text("Revised principle about input validation.", encoding="utf-8")
        new_bundle = _bundle(
            [_candidate("u-1", "Revised principle about input validation.")]
        )

        import book2skill.cli as cli_mod

        original_init = cli_mod.UpdateUseCase.__init__

        def patched_init(self, data_home, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.setdefault("analyze_use_case", _FakeAnalyze(new_bundle))
            original_init(self, data_home, **kwargs)

        monkeypatch.setattr(cli_mod.UpdateUseCase, "__init__", patched_init)

        result = runner.invoke(
            app,
            [
                "update", str(skill_dir), str(src),
                "--data-home", str(data_home),
                "--confirm", "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["published"] is True
        assert payload["snapshot"] is not None
        assert payload["collection_id"] == "col-demo"

    def test_missing_data_home_errors(self, tmp_path: Path) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        _, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        src = tmp_path / "new.txt"
        src.write_text("x", encoding="utf-8")

        result = runner.invoke(
            app,
            ["update", str(skill_dir), str(src), "--confirm"],
        )
        assert result.exit_code != 0

    def test_rollback_restores_via_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = [_unit("u-1", "v1 principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        v1_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        new_bundle = _bundle(
            [_candidate("u-1", "v2 principle about input validation.")]
        )
        import book2skill.cli as cli_mod

        original_init = cli_mod.UpdateUseCase.__init__

        def patched_init(self, data_home, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.setdefault("analyze_use_case", _FakeAnalyze(new_bundle))
            original_init(self, data_home, **kwargs)

        monkeypatch.setattr(cli_mod.UpdateUseCase, "__init__", patched_init)
        runner.invoke(
            app,
            ["update", str(skill_dir), str(data_home / "x.txt"),
             "--data-home", str(data_home), "--confirm", "--json"],
        )
        # Now rollback.
        result = runner.invoke(
            app,
            [
                "update", str(skill_dir),
                "--data-home", str(data_home), "--rollback", "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == v1_md
