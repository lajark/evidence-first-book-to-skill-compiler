"""Tests for the Update / Fold-in use case (TASK-015, PRD FR-03-4).

Covers plan (diff + merge), execute (dry-run vs confirm), override
preservation, removed-unit deprecation, idempotency, meta round-trip and the
CLI ``update`` command. Hermetic: tmp_path data_home, in-memory units/spec,
and a fake AnalyzeUseCase so the new-side bundle is deterministic.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from book2skill.application.analyze import AnalyzeResult
from book2skill.application.diff import Override, OverrideField
from book2skill.application.gate import GateError
from book2skill.application.models import (
    AnalysisBundle,
    CandidateUnit,
    StructureEntry,
)
from book2skill.application.publisher import Publisher, load_skill_meta
from book2skill.application.update import UpdateUseCase
from book2skill.application.update_transaction import UpdateTransactionStore
from book2skill.cli import app
from book2skill.compiler import SkillSpec
from book2skill.domain import (
    ConflictRecord,
    ConflictStatus,
    ExtractionMapEntry,
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    Locator,
    LocatorKind,
    ReviewItem,
    SourceFormat,
    SourceManifest,
)
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.storage import FileRawStorage, KnowledgeSchemaStorage, OverrideStorage

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
        source_refs=[KnowledgeRef(source_id="source-1", block_id="b1")],
        confidence=0.8,
        review_status=status,
        record_version=version,
    )


def _candidate(
    unit_id: str,
    content: str,
    *,
    kind: str = "principle",
    source_id: str = "source-1",
) -> CandidateUnit:
    return CandidateUnit(
        unit_id=unit_id,
        kind=kind,
        content=content,
        source_refs=[{"source_id": source_id, "block_id": "b1"}],
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
        source_ids=source_ids or ["source-1"],
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

    def __init__(
        self,
        bundle: AnalysisBundle,
        *,
        errors: list[GateError] | None = None,
    ) -> None:
        self._result = AnalyzeResult(bundle=bundle, errors=errors or [])
        self.persist_raw_values: list[bool] = []

    def execute(
        self,
        inputs: list[str],
        *,
        collection_id: str | None = None,
        rights_note: str | None = None,
        persist_raw: bool = True,
    ) -> AnalyzeResult:
        self.persist_raw_values.append(persist_raw)
        return self._result


def _seed_raw(
    data_home: Path,
    *,
    source_id: str = "source-1",
    block_id: str = "b1",
) -> SourceManifest:
    raw = FileRawStorage(data_home)
    original = b"trusted source content"
    manifest = SourceManifest(
        source_id=source_id,
        version=1,
        original_name="source.txt",
        content_sha256=hashlib.sha256(original).hexdigest(),
        format=SourceFormat.TXT,
        rights_confirmed=True,
        ingested_at=dt.datetime(2026, 7, 29, tzinfo=dt.UTC),
    )
    raw.save_original(source_id, 1, original, "source.txt")
    raw.save_manifest(manifest)
    raw.save_extraction_map(
        source_id,
        1,
        [
            ExtractionMapEntry(
                block_id=block_id,
                source_id=source_id,
                text_sha256=hashlib.sha256(b"trusted block").hexdigest(),
                locator=Locator(kind=LocatorKind.PARAGRAPH, paragraph=1),
            )
        ],
    )
    return manifest


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
    manifest = _seed_raw(data_home)
    schema = KnowledgeSchemaStorage(data_home)
    for u in old_units:
        schema.save_unit(collection_id, u)

    skill_dir = data_home / "skills" / spec.name
    Publisher(data_home).publish(
        skill_dir,
        old_units,
        spec,
        collection_id=collection_id,
        source_manifests=[manifest],
        unresolved_conflicts=[],
    )
    return data_home, skill_dir


# ---------------------------------------------------------------------------
# plan()
# ---------------------------------------------------------------------------


class TestPlan:
    def test_update_passes_router_adapter_to_analyze_layer(
        self, tmp_path: Path
    ) -> None:
        """Balanced routing must reach the analyze layer.

        Regression: the CLI used to pass ``adapter.config`` to
        UpdateUseCase, collapsing every Map call onto the default profile.
        """
        from book2skill.llm.profiles import (
            ProviderProfile,
            ProviderProfileSet,
        )
        from book2skill.llm.router import RouterLLMAdapter

        profiles = [
            ProviderProfile.model_validate(
                {
                    "profile_id": "ch-a",
                    "provider": "mock",
                    "model": "mock-rule-based-v1",
                    "api_key_env": "MOCK_A",
                    "roles": ["map", "synthesis", "skill"],
                }
            ),
            ProviderProfile.model_validate(
                {
                    "profile_id": "ch-b",
                    "provider": "mock",
                    "model": "mock-rule-based-v1",
                    "api_key_env": "MOCK_B",
                    "roles": ["map"],
                }
            ),
        ]
        profile_set = ProviderProfileSet(
            profiles=profiles, default_profile="ch-a"
        )
        adapter = RouterLLMAdapter(profile_set, data_home=tmp_path / "data")
        use_case = UpdateUseCase(tmp_path / "data", llm=adapter)
        assert use_case._analyze._runtime_llm is adapter
        assert use_case._analyze._llm is adapter

    def test_fold_in_adds_and_modifies_without_removing_old_units(
        self, tmp_path: Path
    ) -> None:
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
        assert suggestion.removed == []
        assert suggestion.merge is not None
        assert {u.unit_id for u in suggestion.merge.merged} == {
            "u-1",
            "u-2",
            "u-3",
        }
        assert suggestion.has_changes

    def test_completely_new_source_keeps_old_active_units(
        self, tmp_path: Path
    ) -> None:
        old = [_unit("u-old", "Existing knowledge must remain active.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        new_bundle = _bundle(
            [
                _candidate(
                    "u-new",
                    "Knowledge from a completely new source.",
                    source_id="source-new",
                )
            ],
            source_ids=["source-new"],
        )
        use_case = UpdateUseCase(
            data_home, analyze_use_case=_FakeAnalyze(new_bundle)
        )

        suggestion = use_case.plan(skill_dir, ["new.txt"])

        assert suggestion.removed == []
        assert suggestion.merge is not None
        assert [u.unit_id for u in suggestion.merge.merged] == ["u-old", "u-new"]

    def test_explicit_source_replacement_allows_removal(
        self, tmp_path: Path
    ) -> None:
        old = [
            _unit("u-1", "Knowledge retained by the replacement."),
            _unit("u-2", "Knowledge absent from the replacement."),
        ]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        new_bundle = _bundle(
            [_candidate("u-1", "Knowledge retained by the replacement.")]
        )
        use_case = UpdateUseCase(
            data_home, analyze_use_case=_FakeAnalyze(new_bundle)
        )

        suggestion = use_case.plan(
            skill_dir, ["replacement.txt"], replace_sources=True
        )

        assert {u.unit_id for u in suggestion.removed} == {"u-2"}
        assert suggestion.merge is not None
        assert {u.unit_id for u in suggestion.merge.merged} == {"u-1"}

    def test_override_preserved_in_merge(self, tmp_path: Path) -> None:
        old = [_unit("u-1", "Original content for the principle rule.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")

        # Human override on u-1's content.
        ov_storage = OverrideStorage(data_home)
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
        analyze = _FakeAnalyze(new_bundle)
        use_case = UpdateUseCase(data_home, analyze_use_case=analyze)

        result = use_case.execute(skill_dir, ["new.txt"], confirm=False)

        assert result.published is False
        assert result.reason == "dry_run"
        # Nothing changed on disk.
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == v1_md
        assert not (data_home / "snapshots").exists() or not list(
            (data_home / "snapshots").glob("*")
        )
        assert analyze.persist_raw_values == [False]

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
        analyze = _FakeAnalyze(new_bundle)
        use_case = UpdateUseCase(data_home, analyze_use_case=analyze)

        result = use_case.execute(skill_dir, ["new.txt"], _spec(), confirm=True)

        assert result.published is True
        assert result.publish_record is not None
        assert result.publish_record.snapshot_path is not None
        assert result.publish_record.snapshot_path.exists()
        assert analyze.persist_raw_values == [True]
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
        use_case.execute(
            skill_dir,
            ["replacement.txt"],
            _spec(),
            confirm=True,
            replace_sources=True,
        )

        history = schema.load_unit_history("col-demo", "u-2")
        assert len(history) == 2  # original + superseded
        latest = schema.load_unit("col-demo", "u-2")
        assert latest is not None
        assert str(latest.review_status) == "superseded"

    def test_replacement_refuses_to_drop_a_unit_with_active_override(
        self, tmp_path: Path
    ) -> None:
        old = [
            _unit("u-1", "Keep this principle."),
            _unit("u-2", "Do not discard this human-edited technique."),
        ]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        OverrideStorage(data_home).save_override(
            "col-demo",
            Override(
                override_id="protect-u-2",
                unit_id="u-2",
                field=OverrideField.CONTENT,
                value="Human-reviewed replacement content.",
                reason="Retain the reviewed technique until replaced explicitly.",
                reviewer="reviewer",
                created_at=dt.datetime(2026, 7, 29, tzinfo=dt.UTC),
            ),
        )
        use_case = UpdateUseCase(
            data_home,
            analyze_use_case=_FakeAnalyze(
                _bundle([_candidate("u-1", "Keep this principle.")])
            ),
        )

        with pytest.raises(DomainError) as exc_info:
            use_case.execute(
                skill_dir,
                ["replacement.txt"],
                _spec(),
                confirm=True,
                replace_sources=True,
            )

        assert exc_info.value.code == ErrorCode.PUBLISH_FAILED
        assert exc_info.value.details["preserved_override_ids"] == ["protect-u-2"]
        history = KnowledgeSchemaStorage(data_home).load_unit_history("col-demo", "u-2")
        assert len(history) == 1

    def test_partial_replacement_cannot_confirm_removals(
        self, tmp_path: Path
    ) -> None:
        old = [
            _unit("u-1", "Knowledge extracted from the surviving source."),
            _unit("u-2", "Knowledge from the source that failed analysis."),
        ]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        schema = KnowledgeSchemaStorage(data_home)
        new_bundle = _bundle(
            [_candidate("u-1", "Knowledge extracted from the surviving source.")]
        )
        errors = [
            GateError(
                path=Path("failed.txt"),
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                message="Source failed during analysis.",
                recovery="Restore the source and retry.",
            )
        ]
        use_case = UpdateUseCase(
            data_home,
            analyze_use_case=_FakeAnalyze(new_bundle, errors=errors),
        )

        with pytest.raises(DomainError) as exc_info:
            use_case.execute(
                skill_dir,
                ["surviving.txt", "failed.txt"],
                _spec(),
                confirm=True,
                replace_sources=True,
            )

        assert exc_info.value.code == ErrorCode.EXTRACT_PARTIAL_FAILURE
        assert len(schema.load_unit_history("col-demo", "u-2")) == 1

    def test_open_review_item_blocks_confirm_before_schema_write(
        self, tmp_path: Path
    ) -> None:
        old = [_unit("u-1", "Original reviewed knowledge.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        schema = KnowledgeSchemaStorage(data_home)
        bundle = _bundle([_candidate("u-1", "Revised knowledge.")])
        bundle = bundle.model_copy(
            update={
                "review_queue": [
                    ReviewItem(
                        item_id="review-1",
                        ref_type="candidate_unit",
                        ref_id="u-1",
                        reason="low_confidence",
                        severity="warning",
                    )
                ]
            }
        )
        use_case = UpdateUseCase(
            data_home, analyze_use_case=_FakeAnalyze(bundle)
        )

        with pytest.raises(DomainError) as exc_info:
            use_case.execute(skill_dir, ["new.txt"], _spec(), confirm=True)

        assert exc_info.value.code == ErrorCode.PUBLISH_FAILED
        assert exc_info.value.details["review_item_ids"] == ["review-1"]
        assert len(schema.load_unit_history("col-demo", "u-1")) == 1

    def test_open_analysis_conflict_blocks_confirm_before_schema_write(
        self, tmp_path: Path
    ) -> None:
        old = [_unit("u-1", "Original reviewed knowledge.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        schema = KnowledgeSchemaStorage(data_home)
        bundle = _bundle([_candidate("u-1", "Revised knowledge.")])
        bundle = bundle.model_copy(
            update={
                "conflicts": [
                    ConflictRecord(
                        conflict_id="conflict-1",
                        unit_ids=["u-1", "u-2"],
                        description="Two source views require review.",
                        status=ConflictStatus.OPEN,
                    )
                ]
            }
        )
        use_case = UpdateUseCase(
            data_home, analyze_use_case=_FakeAnalyze(bundle)
        )

        with pytest.raises(DomainError) as exc_info:
            use_case.execute(skill_dir, ["new.txt"], _spec(), confirm=True)

        assert exc_info.value.code == ErrorCode.PUBLISH_FAILED
        assert exc_info.value.details["conflict_ids"] == ["conflict-1"]
        assert len(schema.load_unit_history("col-demo", "u-1")) == 1

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
        assert str(history[1].review_status) == "approved"

    def test_schema_failure_rolls_back_new_skill_without_partial_history(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        old_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        schema = KnowledgeSchemaStorage(data_home)
        bundle = _bundle(
            [_candidate("u-1", "Revised principle about input validation.")]
        )

        def fail_batch(_collection_id: str, _units: list[KnowledgeUnit]) -> Path:
            raise OSError("simulated atomic schema failure")

        monkeypatch.setattr(schema, "save_units_atomic", fail_batch)
        use_case = UpdateUseCase(
            data_home,
            analyze_use_case=_FakeAnalyze(bundle),
            schema_storage=schema,
        )

        with pytest.raises(DomainError) as exc_info:
            use_case.execute(skill_dir, ["new.txt"], _spec(), confirm=True)

        assert exc_info.value.code == ErrorCode.PUBLISH_FAILED
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == old_md
        assert len(schema.load_unit_history("col-demo", "u-1")) == 1
        assert not list((data_home / ".transactions" / "update").glob("*.json"))

    def test_pending_published_transaction_is_recovered_on_startup(
        self, tmp_path: Path
    ) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        manifest = _seed_raw(data_home)
        newer = [_unit("u-1", "Uncommitted revised principle.")]
        record = Publisher(data_home).publish(
            skill_dir,
            newer,
            _spec(),
            collection_id="col-demo",
            source_manifests=[manifest],
            unresolved_conflicts=[],
            transaction_id="tx-recovery",
        )
        assert record.snapshot_path is not None
        store = UpdateTransactionStore(data_home)
        transaction = store.begin(
            "col-demo", skill_dir, pending_records=[("u-1", 2)]
        )
        transaction = transaction.model_copy(
            update={
                "transaction_id": "tx-recovery",
                "state": "skill_published",
                "published_at": record.published_at,
                "snapshot_path": str(record.snapshot_path),
            }
        )
        store.save(transaction)

        UpdateUseCase(data_home)

        restored_meta = load_skill_meta(skill_dir)
        assert restored_meta is not None
        assert restored_meta.transaction_id is None
        assert not list((data_home / ".transactions" / "update").glob("*.json"))

    def test_first_publish_without_snapshot_is_removed_on_recovery(
        self, tmp_path: Path
    ) -> None:
        data_home = tmp_path / "data"
        manifest = _seed_raw(data_home)
        spec = _spec()
        skill_dir = data_home / "skills" / spec.name
        unit = _unit("u-first", "Uncommitted first publication.")
        record = Publisher(data_home).publish(
            skill_dir,
            [unit],
            spec,
            collection_id="col-first",
            source_manifests=[manifest],
            unresolved_conflicts=[],
            transaction_id="tx-first",
        )
        assert record.snapshot_path is None

        store = UpdateTransactionStore(data_home)
        transaction = store.begin(
            "col-first", skill_dir, pending_records=[("u-first", 1)]
        )
        store.save(
            transaction.model_copy(
                update={
                    "transaction_id": "tx-first",
                    "state": "skill_published",
                    "published_at": record.published_at,
                }
            )
        )

        UpdateUseCase(data_home)

        assert not skill_dir.exists()
        assert not list((data_home / ".transactions" / "update").glob("*.json"))

    def test_recovery_keeps_skill_when_schema_batch_already_committed(
        self, tmp_path: Path
    ) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        manifest = _seed_raw(data_home)
        newer = [_unit("u-1", "Committed revised principle.", version=2)]
        record = Publisher(data_home).publish(
            skill_dir,
            newer,
            _spec(),
            collection_id="col-demo",
            source_manifests=[manifest],
            unresolved_conflicts=[],
            transaction_id="tx-committed",
        )
        schema = KnowledgeSchemaStorage(data_home)
        schema.save_unit("col-demo", newer[0])
        store = UpdateTransactionStore(data_home)
        transaction = store.begin(
            "col-demo", skill_dir, pending_records=[("u-1", 2)]
        )
        transaction = transaction.model_copy(
            update={
                "transaction_id": "tx-committed",
                "state": "skill_published",
                "published_at": record.published_at,
                "snapshot_path": str(record.snapshot_path),
            }
        )
        store.save(transaction)

        UpdateUseCase(data_home)

        meta = load_skill_meta(skill_dir)
        assert meta is not None
        assert meta.transaction_id == "tx-committed"
        assert not list((data_home / ".transactions" / "update").glob("*.json"))

    def test_recovery_rolls_back_schema_and_active_pointer_on_index_drift(
        self, tmp_path: Path
    ) -> None:
        old = [_unit("u-1", "Original principle about input validation.")]
        data_home, skill_dir = _seed_and_publish(tmp_path, old, _spec(), "col-demo")
        old_skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        manifest = _seed_raw(data_home)
        schema = KnowledgeSchemaStorage(data_home)
        store = UpdateTransactionStore(data_home)
        transaction = store.begin(
            "col-demo", skill_dir, pending_records=[("u-1", 2)]
        )
        newer = [_unit("u-1", "Uncommitted revised principle.", version=2)]
        record = Publisher(data_home).publish(
            skill_dir,
            newer,
            _spec(),
            collection_id="col-demo",
            source_manifests=[manifest],
            unresolved_conflicts=[],
            transaction_id=transaction.transaction_id,
        )
        assert record.snapshot_path is not None
        schema.save_unit("col-demo", newer[0])

        pointer = data_home / ".active" / "demo-skill.json"
        index = data_home / "wiki" / "index.md"
        schema_path = data_home / "schema" / "col-demo" / "units.jsonl"
        transaction = transaction.model_copy(
            update={
                "state": "schema_committed",
                "published_at": record.published_at,
                "snapshot_path": str(record.snapshot_path),
                "artifact_id": record.artifact_id,
                "publish_id": record.publish_id,
                "schema_sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
                "active_pointer_sha256": hashlib.sha256(
                    pointer.read_bytes()
                ).hexdigest(),
                "publish_index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
            }
        )
        store.save(transaction)
        index.write_text(
            index.read_text(encoding="utf-8") + "\n# tampered\n",
            encoding="utf-8",
        )

        UpdateUseCase(data_home)

        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == old_skill
        history = schema.load_unit_history("col-demo", "u-1")
        assert len(history) == 1
        restored_pointer = json.loads(pointer.read_text(encoding="utf-8"))
        assert restored_pointer["version_id"] != record.artifact_id
        assert not list((data_home / ".transactions" / "update").glob("*.json"))

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
