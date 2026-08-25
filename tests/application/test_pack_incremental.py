"""Production Pack-only Update bridge tests (B2S-M13-22)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from book2skill.application.models import AnalysisBundle, CandidateUnit
from book2skill.application.pack_incremental import asset_candidates_from_analysis
from book2skill.application.update import UpdateUseCase
from book2skill.domain import SourceFormat, SourceManifest
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.runtime.closure import ProvenanceRef
from book2skill.runtime.pack_store import PersistentPackStore
from book2skill.runtime.pack_update import (
    AssetCandidate,
    AssetPackRelease,
    AssetRecord,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _ref(source_id: str, locator: str) -> ProvenanceRef:
    return ProvenanceRef(
        source_id=source_id,
        locator=locator,
        source_sha256=_hash(source_id),
    )


def _base() -> AssetPackRelease:
    return AssetPackRelease(
        pack_id="pack.plan",
        version="1.0.0",
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        skill_kernel_id="kernel.plan",
        skill_kernel_version="1.0.0",
        skill_kernel_sha256=_hash("stable kernel"),
        assets=[
            AssetRecord(
                canonical_key="method.plan",
                content_sha256=_hash("same method"),
                scope=["general"],
                provenance=[_ref("book-a", "block:p10")],
            )
        ],
    ).with_hash()


def _asset(
    source_id: str,
    *,
    key: str,
    content: str,
    scope: list[str] | None = None,
) -> AssetCandidate:
    return AssetCandidate(
        asset_id=f"{source_id}:{key}",
        canonical_key=key,
        content_sha256=_hash(content),
        scope=scope or ["general"],
        provenance=[_ref(source_id, f"block:{source_id}-p1")],
    )


def _store(tmp_path: Path) -> tuple[PersistentPackStore, AssetPackRelease]:
    store = PersistentPackStore(tmp_path / "packs")
    base = _base()
    store.save_release(base)
    store.activate(base)
    return store, base


def _manifest(source_id: str, content: str) -> SourceManifest:
    from datetime import datetime, timezone

    return SourceManifest(
        source_id=source_id,
        version=1,
        original_name=f"{source_id}.epub",
        content_sha256=_hash(content),
        format=SourceFormat.EPUB,
        rights_note="authorized test source",
        ingested_at=datetime.now(timezone.utc),
    )


def test_analysis_projection_preserves_source_hash_and_skips_rejected_units() -> None:
    bundle = AnalysisBundle(
        collection_id="col.real",
        source_ids=["source-a", "source-b"],
        structure=[],
        candidate_units=[
            CandidateUnit(
                unit_id="unit-a",
                kind="principle",
                content="Keep inputs stable.",
                    source_refs=[
                        {"source_id": "source-a", "block_id": "source-a-p1"}
                    ],
                confidence=0.9,
                review_status="reviewed",
            ),
            CandidateUnit(
                unit_id="unit-rejected",
                kind="principle",
                content="Do not publish this.",
                source_refs=[
                    {"source_id": "source-b", "block_id": "source-b-p1"}
                ],
                confidence=0.2,
                review_status="rejected",
            ),
        ],
        review_queue=[],
    )

    assets = asset_candidates_from_analysis(
        bundle,
        [
            _manifest("source-a", "book-a content"),
            _manifest("source-b", "book-b content"),
        ],
    )

    assert [asset.asset_id for asset in assets] == ["unit-a"]
    assert assets[0].provenance[0].locator == "block:source-a-p1"
    assert assets[0].provenance[0].source_sha256 == _hash("book-a content")


def test_analysis_projection_rejects_uncovered_source_reference() -> None:
    bundle = AnalysisBundle(
        collection_id="col.real",
        source_ids=["source-a"],
        structure=[],
        candidate_units=[
            CandidateUnit(
                unit_id="unit-a",
                kind="principle",
                content="Keep inputs stable.",
                source_refs=[{"source_id": "missing-source", "block_id": "p1"}],
                confidence=0.9,
                review_status="reviewed",
            )
        ],
        review_queue=[],
    )

    with pytest.raises(DomainError) as exc_info:
        asset_candidates_from_analysis(bundle, [_manifest("source-a", "book-a")])

    assert exc_info.value.code == ErrorCode.PACK_UPDATE_INVALID


def test_update_pack_only_replays_two_books_without_touching_kernel(
    tmp_path: Path,
) -> None:
    store, base = _store(tmp_path)
    update = UpdateUseCase(tmp_path / "data")

    result = update.publish_pack_incremental(
        store,
        base,
        candidate_id="books-b-c",
        assets=[
            _asset("book-b", key="template.plan", content="template"),
            _asset("book-c", key="method.plan", content="same method"),
        ],
        reviewer="reviewer-1",
        version="1.1.0",
        confirm=True,
        regression=lambda release: release.skill_kernel_sha256
        == base.skill_kernel_sha256,
    )

    assert result.release is not None
    assert result.release.version == "1.1.0"
    assert result.release.skill_kernel_sha256 == base.skill_kernel_sha256
    assert store.active(base.pack_id).pack_hash == result.release.pack_hash
    assert {asset.canonical_key for asset in result.release.assets} == {
        "method.plan",
        "template.plan",
    }
    assert [item.version for item in store.list_releases(base.pack_id)] == [
        "1.0.0",
        "1.1.0",
    ]


def test_pack_only_conflict_keeps_active_release(tmp_path: Path) -> None:
    store, base = _store(tmp_path)
    update = UpdateUseCase(tmp_path / "data")

    with pytest.raises(DomainError) as exc_info:
        update.publish_pack_incremental(
            store,
            base,
            candidate_id="conflicting-book",
            assets=[
                _asset(
                    "book-b",
                    key="method.plan",
                    content="different method",
                )
            ],
            reviewer="reviewer-1",
            version="1.1.0",
            confirm=True,
            regression=lambda _release: True,
        )

    assert exc_info.value.code == ErrorCode.PACK_UPDATE_CONFLICT
    assert store.active(base.pack_id).pack_hash == base.pack_hash


def test_pack_only_stale_base_is_rejected_without_pointer_change(
    tmp_path: Path,
) -> None:
    store, base = _store(tmp_path)
    update = UpdateUseCase(tmp_path / "data")
    update.publish_pack_incremental(
        store,
        base,
        candidate_id="first-book",
        assets=[_asset("book-b", key="template.plan", content="template")],
        reviewer="reviewer-1",
        version="1.1.0",
        confirm=True,
        regression=lambda _release: True,
    )
    active_before = store.active(base.pack_id)

    with pytest.raises(DomainError) as exc_info:
        update.publish_pack_incremental(
            store,
            base,
            candidate_id="stale-book",
            assets=[_asset("book-c", key="checklist.plan", content="checklist")],
            reviewer="reviewer-1",
            version="1.2.0",
            confirm=True,
            regression=lambda _release: True,
        )

    assert exc_info.value.code == ErrorCode.PACK_UPDATE_CONTRACT_MISMATCH
    assert store.active(base.pack_id).pack_hash == active_before.pack_hash


def test_pack_only_regression_failure_keeps_active_and_candidate_evidence(
    tmp_path: Path,
) -> None:
    store, base = _store(tmp_path)
    update = UpdateUseCase(tmp_path / "data")

    with pytest.raises(DomainError) as exc_info:
        update.publish_pack_incremental(
            store,
            base,
            candidate_id="regressed-book",
            assets=[_asset("book-b", key="template.plan", content="template")],
            reviewer="reviewer-1",
            version="1.1.0",
            confirm=True,
            regression=lambda _release: False,
        )

    assert exc_info.value.code == ErrorCode.PACK_UPDATE_REGRESSION_FAILED
    assert store.active(base.pack_id).pack_hash == base.pack_hash
    assert store.load_candidate(base.pack_id, "regressed-book").stage == "approved"


def test_pack_only_review_can_stop_before_release_activation(tmp_path: Path) -> None:
    store, base = _store(tmp_path)
    update = UpdateUseCase(tmp_path / "data")

    result = update.publish_pack_incremental(
        store,
        base,
        candidate_id="review-only",
        assets=[_asset("book-b", key="template.plan", content="template")],
        reviewer="reviewer-1",
        version="1.1.0",
        confirm=False,
        regression=lambda _release: True,
    )

    assert result.release is None
    assert result.candidate.stage == "approved"
    assert store.active(base.pack_id).pack_hash == base.pack_hash
    assert store.list_releases(base.pack_id)[0].version == base.version
