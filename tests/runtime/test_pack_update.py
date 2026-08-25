"""B2S-M13-03: same-task Asset Pack incremental publication tests."""

from __future__ import annotations

import hashlib

import pytest

from book2skill.runtime.closure import ProvenanceRef
from book2skill.runtime.pack_update import (
    AssetCandidate,
    AssetPackRelease,
    AssetRecord,
    PackPublisher,
    PackStage,
    PackUpdateError,
    ReviewDecision,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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
                provenance=[
                    ProvenanceRef(source_id="book-a", locator="p10")
                ],
            )
        ],
    ).with_hash()


def _candidate_asset(
    *,
    asset_id: str = "asset-template",
    canonical_key: str = "report.template",
    content: str = "new template",
    source_id: str = "book-b",
) -> AssetCandidate:
    return AssetCandidate(
        asset_id=asset_id,
        canonical_key=canonical_key,
        content_sha256=_hash(content),
        scope=["general"],
        provenance=[ProvenanceRef(source_id=source_id, locator="p20")],
    )


def _approve(candidate):
    publisher = PackPublisher()
    reviewed = publisher.review(
        candidate,
        [
            ReviewDecision(
                asset_id=asset.asset_id,
                decision="approve",
                reviewer="human-reviewer",
            )
            for asset in candidate.assets
        ],
    )
    return publisher.approve(reviewed)


def test_compatible_second_source_publishes_pack_without_kernel_change() -> None:
    base = _base_pack()
    publisher = PackPublisher()
    candidate = publisher.create_candidate(
        base,
        candidate_id="candidate-book-b-1",
        assets=[
            _candidate_asset(),
            AssetCandidate(
                asset_id="asset-duplicate",
                canonical_key="method.plan",
                content_sha256=_hash("same method"),
                scope=["general"],
                provenance=[
                    ProvenanceRef(source_id="book-b", locator="p11")
                ],
            ),
        ],
    )
    approved = _approve(candidate)
    release = publisher.publish(
        base, approved, version="1.1.0", regression=lambda _: True
    )

    assert candidate.stage == PackStage.CANDIDATE
    assert release.version == "1.1.0"
    assert release.previous_version == base.version
    assert release.skill_kernel_sha256 == base.skill_kernel_sha256
    assert release.task_contract_id == base.task_contract_id
    assert len(release.assets) == 2
    duplicate = next(
        item for item in release.assets if item.canonical_key == "method.plan"
    )
    assert {item.source_id for item in duplicate.provenance} == {"book-a", "book-b"}
    assert base.version == "1.0.0"


def test_candidate_requires_a_decision_for_every_asset() -> None:
    base = _base_pack()
    candidate = PackPublisher().create_candidate(
        base, candidate_id="candidate-1", assets=[_candidate_asset()]
    )

    with pytest.raises(PackUpdateError) as exc_info:
        PackPublisher().review(candidate, [])

    assert exc_info.value.code == "PACK_UPDATE_REVIEW_REQUIRED"


def test_rejected_candidate_cannot_be_published() -> None:
    base = _base_pack()
    publisher = PackPublisher()
    candidate = publisher.create_candidate(
        base, candidate_id="candidate-1", assets=[_candidate_asset()]
    )
    reviewed = publisher.review(
        candidate,
        [
            ReviewDecision(
                asset_id="asset-template",
                decision="reject",
                reviewer="human-reviewer",
                reason="out of scope",
            )
        ],
    )
    rejected = publisher.approve(reviewed)

    with pytest.raises(PackUpdateError) as exc_info:
        publisher.publish(base, rejected, version="1.1.0", regression=lambda _: True)

    assert rejected.stage == PackStage.REJECTED
    assert exc_info.value.code == "PACK_UPDATE_REVIEW_REQUIRED"


def test_conflicting_same_key_blocks_publication_and_keeps_base_unchanged() -> None:
    base = _base_pack()
    publisher = PackPublisher()
    candidate = publisher.create_candidate(
        base,
        candidate_id="candidate-conflict",
        assets=[
            _candidate_asset(
                asset_id="asset-conflict",
                canonical_key="method.plan",
                content="different method",
            )
        ],
    )
    approved = _approve(candidate)

    with pytest.raises(PackUpdateError) as exc_info:
        publisher.publish(base, approved, version="1.1.0", regression=lambda _: True)

    assert exc_info.value.code == "PACK_UPDATE_CONFLICT"
    assert base.version == "1.0.0"
    assert base.pack_hash == base.compute_hash()


def test_incompatible_kernel_candidate_is_rejected() -> None:
    base = _base_pack()
    publisher = PackPublisher()
    candidate = publisher.create_candidate(
        base, candidate_id="candidate-1", assets=[_candidate_asset()]
    ).model_copy(update={"skill_kernel_sha256": _hash("changed kernel")})
    approved = _approve(candidate)

    with pytest.raises(PackUpdateError) as exc_info:
        publisher.publish(base, approved, version="1.1.0", regression=lambda _: True)

    assert exc_info.value.code == "PACK_UPDATE_CONTRACT_MISMATCH"


def test_regression_failure_does_not_publish() -> None:
    base = _base_pack()
    publisher = PackPublisher()
    approved = _approve(
        publisher.create_candidate(
            base, candidate_id="candidate-1", assets=[_candidate_asset()]
        )
    )

    with pytest.raises(PackUpdateError) as exc_info:
        publisher.publish(base, approved, version="1.1.0", regression=lambda _: False)

    assert exc_info.value.code == "PACK_UPDATE_REGRESSION_FAILED"


def test_rollback_selects_prior_production_pack_with_same_kernel() -> None:
    base = _base_pack()
    publisher = PackPublisher()
    approved = _approve(
        publisher.create_candidate(
            base, candidate_id="candidate-1", assets=[_candidate_asset()]
        )
    )
    release = publisher.publish(
        base, approved, version="1.1.0", regression=lambda _: True
    )

    rollback = publisher.rollback(release, base)

    assert rollback.from_version == "1.1.0"
    assert rollback.to_version == "1.0.0"
    assert rollback.selected_pack.pack_hash == base.pack_hash
