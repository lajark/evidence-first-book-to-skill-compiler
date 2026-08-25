"""Persistent Pack pointer and rollback tests for M13 multi-book follow-up."""

from __future__ import annotations

import hashlib

import pytest

import book2skill.runtime.pack_store as pack_store_module
from book2skill.runtime.closure import ProvenanceRef
from book2skill.runtime.pack_store import PackStoreError, PersistentPackStore
from book2skill.runtime.pack_update import (
    AssetCandidate,
    AssetPackRelease,
    AssetRecord,
    PackPublisher,
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
                provenance=[ProvenanceRef(source_id="book-a", locator="p10")],
            )
        ],
    ).with_hash()


def _approved_candidate(base: AssetPackRelease):
    publisher = PackPublisher()
    candidate = publisher.create_candidate(
        base,
        candidate_id="candidate-book-b-1",
        assets=[
            AssetCandidate(
                asset_id="asset-template",
                canonical_key="report.template",
                content_sha256=_hash("new template"),
                scope=["general"],
                provenance=[ProvenanceRef(source_id="book-b", locator="p20")],
            )
        ],
    )
    reviewed = publisher.review(
        candidate,
        [
            ReviewDecision(
                asset_id="asset-template",
                decision="approve",
                reviewer="human-reviewer",
            )
        ],
    )
    return publisher.approve(reviewed)


def test_store_round_trip_and_atomic_pack_only_activation(tmp_path) -> None:
    store = PersistentPackStore(tmp_path / "packs")
    base = _base_pack()
    store.save_release(base)
    store.activate(base)
    candidate = _approved_candidate(base)
    store.save_candidate(candidate)

    release = store.publish_incremental(
        base, candidate, version="1.1.0", regression=lambda _: True
    )
    reopened = PersistentPackStore(tmp_path / "packs")

    assert (
        reopened.load_candidate(base.pack_id, candidate.candidate_id).stage.value
        == "approved"
    )
    assert reopened.active(base.pack_id).pack_hash == release.pack_hash
    assert [item.version for item in reopened.list_releases(base.pack_id)] == [
        "1.0.0",
        "1.1.0",
    ]
    assert (tmp_path / "packs" / base.pack_id / "active.json").is_file()


def test_store_rejects_immutable_release_drift(tmp_path) -> None:
    store = PersistentPackStore(tmp_path / "packs")
    base = _base_pack()
    store.save_release(base)

    changed = base.model_copy(update={"assets": []}).with_hash()
    with pytest.raises(PackStoreError) as exc_info:
        store.save_release(changed)

    assert exc_info.value.code == "PACK_UPDATE_INVALID"


def test_stale_base_is_rejected_without_pointer_change(tmp_path) -> None:
    store = PersistentPackStore(tmp_path / "packs")
    base = _base_pack()
    store.save_release(base)
    store.activate(base)
    candidate = _approved_candidate(base)
    release = PackPublisher().publish(
        base, candidate, version="1.1.0", regression=lambda _: True
    )
    other = release.model_copy(update={"version": "1.2.0"}).with_hash()
    store.save_release(other)

    with pytest.raises(PackStoreError) as exc_info:
        store.activate(other, expected_base_version="1.1.0")

    assert exc_info.value.code == "PACK_UPDATE_CONTRACT_MISMATCH"
    assert store.active(base.pack_id).version == base.version


def test_active_pointer_write_failure_keeps_previous_release(
    tmp_path, monkeypatch
) -> None:
    store = PersistentPackStore(tmp_path / "packs")
    base = _base_pack()
    store.save_release(base)
    store.activate(base)
    candidate = _approved_candidate(base)
    release = PackPublisher().publish(
        base, candidate, version="1.1.0", regression=lambda _: True
    )

    real_atomic_write = pack_store_module.atomic_write

    def fail_active_pointer(path, content):
        if path.name == "active.json":
            raise OSError("simulated active pointer failure")
        return real_atomic_write(path, content)

    monkeypatch.setattr(pack_store_module, "atomic_write", fail_active_pointer)
    with pytest.raises(OSError, match="active pointer"):
        store.activate(release, expected_base_version=base.version)

    assert store.active(base.pack_id).version == base.version


def test_rollback_switches_active_pointer_to_prior_release(tmp_path) -> None:
    store = PersistentPackStore(tmp_path / "packs")
    base = _base_pack()
    store.save_release(base)
    store.activate(base)
    candidate = _approved_candidate(base)
    store.publish_incremental(
        base, candidate, version="1.1.0", regression=lambda _: True
    )

    rollback = store.rollback(base.pack_id, base.version)

    assert rollback.from_version == "1.1.0"
    assert rollback.to_version == base.version
    assert store.active(base.pack_id).pack_hash == base.pack_hash
