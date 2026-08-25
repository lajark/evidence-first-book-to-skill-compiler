"""Pack release ↔ production Closure binding tests (B2S-M13 P0 closeout)."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pytest

from book2skill.application.update import UpdateUseCase
from book2skill.domain import SourceFormat, SourceManifest
from book2skill.domain.errors import ErrorCode
from book2skill.runtime.closure import (
    ClosureLifecycle,
    RuntimeClosureError,
    RuntimeClosureSession,
)
from book2skill.runtime.emission import RuntimeClosureSpec, RuntimeProductEmitter
from book2skill.runtime.pack_closure import PackClosureBinder
from book2skill.runtime.pack_store import PersistentPackStore
from book2skill.runtime.pack_update import (
    AssetCandidate,
    AssetPackRelease,
    AssetRecord,
)
from book2skill.runtime.profiles import GeneratedSkillProduct, ProductProfile


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _manifest() -> SourceManifest:
    return SourceManifest(
        source_id="source-p0-1",
        version=1,
        original_name="authorized.epub",
        content_sha256=_hash("authorized source"),
        format=SourceFormat.EPUB,
        rights_note="authorized P0 source",
        ingested_at=dt.datetime(2026, 8, 25, tzinfo=dt.UTC),
    )


def _product() -> GeneratedSkillProduct:
    return GeneratedSkillProduct(
        product_id="skill.p0",
        task_id="p0",
        task_contract_id="task.p0",
        task_contract_version="1.0.0",
        profile=ProductProfile.STANDALONE,
    )


def _spec() -> RuntimeClosureSpec:
    return RuntimeClosureSpec(
        closure_version="1.0.0",
        skill_kernel_id="kernel.p0",
        skill_kernel_version="1.0.0",
        asset_pack_id="pack.p0",
        asset_pack_version="1.0.0",
        io_schema_id="io.p0",
        io_schema_version="1.0.0",
        security_profile_id="standalone-default",
        security_profile_version="1.0.0",
    )


def _release(version: str, content: str) -> AssetPackRelease:
    return AssetPackRelease(
        pack_id="pack.p0",
        version=version,
        task_contract_id="task.p0",
        task_contract_version="1.0.0",
        skill_kernel_id="kernel.p0",
        skill_kernel_version="1.0.0",
        skill_kernel_sha256=_hash("kernel.p0"),
        assets=[
            AssetRecord(
                canonical_key="principle.p0",
                content_sha256=_hash(content),
                scope=["general"],
                provenance=[
                    {
                        "source_id": "source-p0-1",
                        "locator": "block:source-p0-1-p1",
                        "source_sha256": _manifest().content_sha256,
                    }
                ],
            )
        ],
    ).with_hash()


def _emit(root: Path) -> None:
    (root / "SKILL.md").write_text("# P0\n", encoding="utf-8")
    RuntimeProductEmitter.emit(
        root,
        _product(),
        _spec(),
        lifecycle=ClosureLifecycle.PRODUCTION,
        source_manifests=[_manifest()],
        source_refs=[("source-p0-1", "source-p0-1-p1")],
    )


def test_pack_release_binds_and_reopens_production_closure(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    _emit(root)
    first = _release("1.0.0", "first")

    binding = PackClosureBinder.bind(root, first)
    session = RuntimeClosureSession.open(root)

    assert binding.pack_hash == first.pack_hash
    assert session.manifest.asset_pack_hash == first.pack_hash
    assert session.manifest.asset_pack_version == first.version
    assert session.resource_path(binding.resource_id).name == "pack-release.json"
    assert session.closure_hash == binding.closure_hash
    assert RuntimeClosureSession.open(root).closure_hash == binding.closure_hash


def test_pack_upgrade_and_rollback_rebind_closure_without_kernel_change(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    _emit(root)
    first = _release("1.0.0", "first")
    second = _release("1.1.0", "second")
    store = PersistentPackStore(tmp_path / "packs")
    store.save_release(first)
    store.save_release(second)
    store.activate(first)

    PackClosureBinder.bind(root, first)
    first_session = RuntimeClosureSession.open(root)
    first_hash = first_session.closure_hash
    store.activate(second, expected_base_version=first.version)
    PackClosureBinder.bind(root, second)
    second_session = RuntimeClosureSession.open(root)
    assert second_session.manifest.asset_pack_hash == second.pack_hash
    assert (
        second_session.manifest.skill_kernel_id
        == first_session.manifest.skill_kernel_id
    )
    assert second_session.closure_hash != first_hash

    rollback = UpdateUseCase(tmp_path / "rollback").rollback_pack_incremental(
        store,
        first.pack_id,
        first.version,
        closure_root=root,
    )
    rollback_session = RuntimeClosureSession.open(root)
    assert rollback.selected_pack.version == first.version
    assert rollback_session.manifest.asset_pack_hash == first.pack_hash
    assert rollback_session.manifest.skill_kernel_id == "kernel.p0"


def test_pack_binding_rejects_incompatible_task_contract(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    _emit(root)
    release = _release("1.0.0", "first").model_copy(
        update={"task_contract_id": "task.other"}
    ).with_hash()

    with pytest.raises(RuntimeClosureError) as exc_info:
        PackClosureBinder.bind(root, release)

    assert exc_info.value.code == ErrorCode.RUNTIME_CLOSURE_INVALID
    assert not (root / "assets" / "pack-release.json").exists()


def test_update_pack_only_rebinds_production_closure(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    _emit(root)
    base = _release("1.0.0", "first")
    store = PersistentPackStore(tmp_path / "packs")
    store.save_release(base)
    store.activate(base)

    result = UpdateUseCase(tmp_path / "update").publish_pack_incremental(
        store,
        base,
        candidate_id="candidate-p0",
        assets=[
            AssetCandidate(
                asset_id="asset-new",
                canonical_key="principle.new",
                content_sha256=_hash("new"),
                scope=["general"],
                provenance=[
                    {
                        "source_id": "source-p0-1",
                        "locator": "block:source-p0-1-p2",
                        "source_sha256": _manifest().content_sha256,
                    }
                ],
            )
        ],
        reviewer="authorized-p0-reviewer",
        version="1.1.0",
        confirm=True,
        regression=lambda release: (
            release.skill_kernel_sha256 == base.skill_kernel_sha256
        ),
        closure_root=root,
    )

    assert result.release is not None
    assert result.closure_binding is not None
    session = RuntimeClosureSession.open(root)
    assert session.manifest.asset_pack_hash == result.release.pack_hash
    assert store.active(base.pack_id).pack_hash == result.release.pack_hash
