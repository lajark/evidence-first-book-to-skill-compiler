"""B2S-M13-04: multi-book deduplication and conflict governance tests."""

from __future__ import annotations

import hashlib

import pytest

from book2skill.runtime.closure import ProvenanceRef
from book2skill.runtime.multi_book import (
    MergeDisposition,
    MultiBookGovernance,
    ScopeRelation,
)
from book2skill.runtime.pack_update import (
    AssetCandidate,
    AssetPackRelease,
    AssetRecord,
    PackPublisher,
    PackUpdateError,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _provenance(source_id: str, locator: str) -> ProvenanceRef:
    return ProvenanceRef(
        source_id=source_id,
        locator=locator,
        source_sha256=_hash(source_id),
    )


def _base_pack() -> AssetPackRelease:
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
                provenance=[_provenance("book-a", "p10")],
            )
        ],
    ).with_hash()


def _candidate(
    source_id: str,
    *,
    content: str = "same method",
    scope: list[str] | None = None,
    key: str = "method.plan",
) -> AssetCandidate:
    return AssetCandidate(
        asset_id=f"{source_id}:{key}",
        canonical_key=key,
        content_sha256=_hash(content),
        scope=scope or ["general"],
        provenance=[_provenance(source_id, "p20")],
    )


def test_duplicate_method_from_two_books_keeps_all_sources() -> None:
    plan = MultiBookGovernance().analyze(
        _base_pack(), [_candidate("book-b"), _candidate("book-c")]
    )

    assert len(plan.duplicates) == 2
    assert plan.publishable is True
    refs = plan.provenance_by_key["method.plan"]
    assert {item.source_id for item in refs} == {"book-a", "book-b", "book-c"}
    assert {item.source_sha256 for item in refs} == {
        _hash("book-a"),
        _hash("book-b"),
        _hash("book-c"),
    }


def test_new_assets_from_multiple_books_are_additive() -> None:
    plan = MultiBookGovernance().analyze(
        _base_pack(),
        [
            _candidate("book-b", key="template.plan", content="template"),
            _candidate("book-c", key="checklist.plan", content="checklist"),
        ],
    )

    assert [item.relation for item in plan.comparisons] == [
        ScopeRelation.NEW,
        ScopeRelation.NEW,
    ]
    assert all(item.disposition == MergeDisposition.ADD for item in plan.comparisons)
    plan.assert_publishable()


def test_disjoint_scope_is_preserved_but_requires_explicit_routing() -> None:
    plan = MultiBookGovernance().analyze(
        _base_pack(),
        [_candidate("book-b", content="regulated method", scope=["regulated"])],
    )

    assert len(plan.scoped_variants) == 1
    assert plan.scoped_variants[0].relation == ScopeRelation.DISJOINT
    assert plan.scoped_variants[0].disposition == MergeDisposition.COEXIST_REVIEW
    with pytest.raises(PackUpdateError) as exc_info:
        plan.assert_publishable()
    assert exc_info.value.code == "PACK_UPDATE_SCOPE_REVIEW_REQUIRED"


def test_overlapping_scope_conflict_keeps_both_author_records() -> None:
    plan = MultiBookGovernance().analyze(
        _base_pack(), [_candidate("book-b", content="different method")]
    )
    conflict = plan.conflicts[0]

    assert conflict.relation == ScopeRelation.OVERLAPPING
    assert conflict.disposition == MergeDisposition.CONFLICT_REVIEW
    assert conflict.existing_provenance[0].source_id == "book-a"
    assert conflict.candidate_provenance[0].source_id == "book-b"
    with pytest.raises(PackUpdateError) as exc_info:
        plan.assert_publishable()
    assert exc_info.value.code == "PACK_UPDATE_CONFLICT"


def test_empty_batch_is_rejected() -> None:
    with pytest.raises(PackUpdateError) as exc_info:
        MultiBookGovernance().analyze(_base_pack(), [])

    assert exc_info.value.code == "PACK_UPDATE_INVALID"


def test_pack_candidate_identity_is_checked_before_multi_book_analysis() -> None:
    base = _base_pack()
    candidate = PackPublisher().create_candidate(
        base,
        candidate_id="candidate-1",
        assets=[_candidate("book-b", key="template.plan", content="template")],
    ).model_copy(update={"skill_kernel_sha256": _hash("changed kernel")})

    with pytest.raises(PackUpdateError) as exc_info:
        MultiBookGovernance().analyze(base, candidate)

    assert exc_info.value.code == "PACK_UPDATE_CONTRACT_MISMATCH"
