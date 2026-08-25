"""M13-08: experimental Build/Publish Runtime Product emission."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from book2skill.application.build import BuildUseCase
from book2skill.application.publisher import Publisher
from book2skill.compiler import SkillSpec
from book2skill.domain import (
    KnowledgeStatus,
    KnowledgeUnit,
    SourceFormat,
    SourceManifest,
)
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.runtime import (
    CapabilityKind,
    ClosureLifecycle,
    GeneratedSkillProduct,
    ProductProfile,
    RuntimeClosureError,
    RuntimeClosureSession,
    RuntimeClosureSpec,
    RuntimeProductEmitter,
    RuntimeProductManifest,
    ToolCapability,
)


def _spec() -> RuntimeClosureSpec:
    return RuntimeClosureSpec(
        closure_version="1.0.0",
        skill_kernel_id="kernel.plan",
        skill_kernel_version="1.0.0",
        asset_pack_id="pack.plan",
        asset_pack_version="1.0.0",
        io_schema_id="io.plan",
        io_schema_version="1.0.0",
        security_profile_id="standalone-default",
        security_profile_version="1.0.0",
    )


def _product(*, bundled: bool = False) -> GeneratedSkillProduct:
    capabilities = (
        [
            ToolCapability(
                capability_id="tool:missing",
                kind=CapabilityKind.BUNDLED_SCRIPT,
                version="1.0.0",
                resource_id="tool:missing",
            )
        ]
        if bundled
        else []
    )
    return GeneratedSkillProduct(
        product_id="skill.plan",
        task_id="plan",
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        profile=ProductProfile.STANDALONE,
        capabilities=capabilities,
    )


def _source_manifest() -> SourceManifest:
    return SourceManifest(
        source_id="source-123",
        version=1,
        original_name="fixture.txt",
        content_sha256="a" * 64,
        format=SourceFormat.TXT,
        ingested_at=dt.datetime(2026, 8, 25, tzinfo=dt.UTC),
    )


def _skill_spec() -> SkillSpec:
    return SkillSpec(
        name="plan",
        description="A planning skill for a deterministic fixture.",
        use_when=["When a plan is needed."],
        do_not_use_when=["When no planning is requested."],
    )


def _unit() -> KnowledgeUnit:
    return KnowledgeUnit(
        unit_id="unit-1",
        kind="principle",
        content="Validate the input before planning.",
        source_refs=[{"source_id": "source-123", "block_id": "source-123-p1"}],
        confidence=0.9,
        review_status=KnowledgeStatus.APPROVED,
        record_version=1,
    )


def test_emitter_writes_candidate_descriptor_and_closure(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "guide.md").write_text("guide\n", encoding="utf-8")

    emission = RuntimeProductEmitter.emit(
        tmp_path,
        _product(),
        _spec(),
        lifecycle=ClosureLifecycle.CANDIDATE,
        source_manifests=[_source_manifest()],
    )

    assert emission.closure_manifest.lifecycle == ClosureLifecycle.CANDIDATE
    assert emission.product_manifest.product.product_id == "skill.plan"
    assert (
        emission.product_manifest.manifest_hash
        == emission.product_manifest.compute_hash()
    )
    assert (tmp_path / "runtime-product.json").exists()
    assert (tmp_path / "runtime-closure.json").exists()
    assert {
        resource.path for resource in emission.closure_manifest.resources
    } == {"SKILL.md", "references/guide.md"}
    with pytest.raises(RuntimeClosureError) as exc_info:
        RuntimeClosureSession.open(tmp_path)
    assert exc_info.value.code == ErrorCode.RUNTIME_CLOSURE_CANDIDATE_NOT_PUBLISHED


def test_emitter_production_closure_reopens_and_descriptor_round_trips(
    tmp_path: Path,
) -> None:
    (tmp_path / "SKILL.md").write_text("# Plan\n", encoding="utf-8")

    emission = RuntimeProductEmitter.emit(
        tmp_path,
        _product(),
        _spec(),
        lifecycle=ClosureLifecycle.PRODUCTION,
        source_manifests=[_source_manifest()],
        source_refs=[("source-123", "source-123-p1")],
    )

    session = RuntimeClosureSession.open(tmp_path)
    descriptor = RuntimeProductManifest.from_file(tmp_path / "runtime-product.json")
    assert session.closure_hash == emission.closure_manifest.closure_hash
    assert descriptor.closure_hash == emission.closure_manifest.closure_hash
    assert descriptor == emission.product_manifest
    assert {
        (ref.source_id, ref.locator)
        for resource in emission.closure_manifest.resources
        for ref in resource.provenance
    } == {("source-123", "block:source-123-p1")}


def test_emitter_rejects_bundled_capability_without_pinned_resource(
    tmp_path: Path,
) -> None:
    (tmp_path / "SKILL.md").write_text("# Plan\n", encoding="utf-8")

    with pytest.raises(RuntimeClosureError) as exc_info:
        RuntimeProductEmitter.emit(
            tmp_path,
            _product(bundled=True),
            _spec(),
            lifecycle=ClosureLifecycle.CANDIDATE,
            source_manifests=[_source_manifest()],
        )

    assert exc_info.value.code == ErrorCode.RUNTIME_CLOSURE_CAPABILITY_MISSING


def test_emitter_rejects_missing_source_provenance(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("# Plan\n", encoding="utf-8")

    with pytest.raises(RuntimeClosureError) as exc_info:
        RuntimeProductEmitter.emit(
            tmp_path,
            _product(),
            _spec(),
            lifecycle=ClosureLifecycle.CANDIDATE,
        )

    assert exc_info.value.code == ErrorCode.RUNTIME_CLOSURE_INVALID
    assert exc_info.value.details["reason"] == "source_provenance_missing"


def test_emitter_rejects_duplicate_source_manifests(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("# Plan\n", encoding="utf-8")

    with pytest.raises(RuntimeClosureError) as exc_info:
        RuntimeProductEmitter.emit(
            tmp_path,
            _product(),
            _spec(),
            lifecycle=ClosureLifecycle.CANDIDATE,
            source_manifests=[_source_manifest(), _source_manifest()],
        )

    assert exc_info.value.code == ErrorCode.RUNTIME_CLOSURE_INVALID
    assert exc_info.value.details["reason"] == "duplicate_source_manifest"


def test_emitter_rejects_uncovered_source_block(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("# Plan\n", encoding="utf-8")

    with pytest.raises(RuntimeClosureError) as exc_info:
        RuntimeProductEmitter.emit(
            tmp_path,
            _product(),
            _spec(),
            lifecycle=ClosureLifecycle.CANDIDATE,
            source_manifests=[_source_manifest()],
            source_refs=[("source-unknown", "source-unknown-p1")],
        )

    assert exc_info.value.code == ErrorCode.RUNTIME_CLOSURE_INVALID
    assert exc_info.value.details["reason"] == "source_block_provenance_missing"


def test_build_emits_candidate_only_when_explicitly_configured(tmp_path: Path) -> None:
    source = tmp_path / "fixture.txt"
    source.write_text("Always validate input before planning.", encoding="utf-8")
    product = _product()
    result = BuildUseCase(
        data_home=tmp_path / "data",
        runtime_product=product,
        runtime_closure_spec=_spec(),
    ).build_from_sources(
        [str(source)], _skill_spec(), output_dir=tmp_path / "candidate"
    )

    assert result.skill_dir is not None
    assert result.runtime_closure_manifest is not None
    assert result.runtime_closure_manifest.lifecycle == ClosureLifecycle.CANDIDATE
    assert all(
        reference.locator.startswith("block:")
        for resource in result.runtime_closure_manifest.resources
        for reference in resource.provenance
    )
    assert (result.skill_dir / "runtime-product.json").exists()
    assert RuntimeProductManifest.from_file(
        result.skill_dir / "runtime-product.json"
    ).product == product

    default_result = BuildUseCase(
        data_home=tmp_path / "default-data"
    ).build_from_sources(
        [str(source)], _skill_spec(), output_dir=tmp_path / "default-candidate"
    )
    assert default_result.runtime_closure_manifest is not None
    assert (
        default_result.runtime_closure_manifest.lifecycle
        == ClosureLifecycle.CANDIDATE
    )
    assert default_result.runtime_product_manifest is not None
    assert (
        default_result.runtime_product_manifest.product.product_id == "skill.plan"
    )

    legacy = BuildUseCase().build_from_sources(
        [str(source)], _skill_spec(), output_dir=tmp_path / "legacy"
    )
    assert legacy.skill_dir is not None
    assert not (legacy.skill_dir / "runtime-product.json").exists()
    assert not (legacy.skill_dir / "runtime-closure.json").exists()


def test_built_product_closure_replays_without_project_inputs(tmp_path: Path) -> None:
    """Prove a generated product can execute from its pinned tree alone."""

    source = tmp_path / "fixture.txt"
    source.write_text("Always validate input before planning.", encoding="utf-8")
    build_product = _product()
    product = build_product.model_copy(
        update={
            "capabilities": [
                ToolCapability(
                    capability_id="tool:emit",
                    kind=CapabilityKind.BUNDLED_SCRIPT,
                    version="1.0.0",
                    resource_id="resource:scripts/emit.py",
                )
            ]
        }
    )
    result = BuildUseCase(
        data_home=tmp_path / "data",
        runtime_product=build_product,
        runtime_closure_spec=_spec(),
    ).build_from_sources(
        [str(source)], _skill_spec(), output_dir=tmp_path / "candidate"
    )
    assert result.skill_dir is not None

    scripts = result.skill_dir / "scripts"
    scripts.mkdir()
    script = "\n".join(
        (
            "import hashlib",
            "import json",
            "from pathlib import Path",
            "",
            "root = Path(__file__).resolve().parents[1]",
            "closure = json.loads(",
            '    (root / "runtime-closure.json").read_text(encoding="utf-8")',
            ")",
            'output = root / "task" / "output.json"',
            "output.parent.mkdir(exist_ok=True)",
            "payload = {",
            '    "closure_hash": closure["closure_hash"],',
            '    "skill_hash": hashlib.sha256(',
            '        (root / "SKILL.md").read_bytes()',
            "    ).hexdigest(),",
            "}",
            'output.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")',
        )
    )
    (scripts / "emit.py").write_text(script, encoding="utf-8")
    emission = RuntimeProductEmitter.emit(
        result.skill_dir,
        product,
        _spec(),
        lifecycle=ClosureLifecycle.PRODUCTION,
        source_manifests=[_source_manifest()],
        source_refs=[("source-123", "source-123-p1")],
    )

    session = RuntimeClosureSession.open(result.skill_dir)
    project = tmp_path / "hidden-project"
    for directory in ("Core", "source", "workspace", "output"):
        (project / directory).mkdir(parents=True)
    (project / "source" / "original.txt").write_text("hidden\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    subprocess.run(
        [sys.executable, str(session.resource_path("resource:scripts/emit.py"))],
        cwd=project,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    output = json.loads(
        (result.skill_dir / "task" / "output.json").read_text(encoding="utf-8")
    )
    assert output == {
        "closure_hash": emission.closure_manifest.closure_hash,
        "skill_hash": hashlib.sha256(
            (result.skill_dir / "SKILL.md").read_bytes()
        ).hexdigest(),
    }
    assert not (project / "Core" / "book2skill").exists()
    assert not (project / "source" / "generated").exists()


def test_publisher_emits_production_closure_only_when_configured(
    tmp_path: Path,
) -> None:
    data_home = tmp_path / "data"
    skill_dir = data_home / "skills" / "plan"
    record = Publisher(data_home).publish(
        skill_dir,
        [_unit()],
        _skill_spec(),
        collection_id="collection-plan",
        source_manifests=[_source_manifest()],
        unresolved_conflicts=[],
        runtime_product=_product(),
        runtime_closure_spec=_spec(),
    )

    assert record.runtime_closure_hash is not None
    session = RuntimeClosureSession.open(skill_dir)
    assert session.manifest.lifecycle == ClosureLifecycle.PRODUCTION
    assert session.closure_hash == record.runtime_closure_hash
    assert all(
        reference.locator.startswith("block:")
        for resource in session.manifest.resources
        for reference in resource.provenance
    )


def test_publisher_defaults_to_production_contract(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    skill_dir = data_home / "skills" / "plan"
    record = Publisher(data_home).publish(
        skill_dir,
        [_unit()],
        _skill_spec(),
        collection_id="collection-plan",
        source_manifests=[_source_manifest()],
        unresolved_conflicts=[],
    )

    assert record.runtime_closure_hash is not None
    descriptor = RuntimeProductManifest.from_file(
        skill_dir / "runtime-product.json"
    )
    assert descriptor.product.product_id == "skill.plan"
    assert (
        RuntimeClosureSession.open(skill_dir).manifest.lifecycle
        == ClosureLifecycle.PRODUCTION
    )


def test_build_and_publisher_reject_partial_emission_configuration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fixture.txt"
    source.write_text("Always validate input before planning.", encoding="utf-8")

    with pytest.raises(DomainError) as build_error:
        BuildUseCase(runtime_product=_product()).build_from_sources(
            [str(source)], _skill_spec(), output_dir=tmp_path / "candidate"
        )
    assert build_error.value.code == ErrorCode.RUNTIME_CLOSURE_INVALID

    with pytest.raises(DomainError) as publish_error:
        Publisher(tmp_path / "publish-data").publish(
            tmp_path / "publish-data" / "skills" / "plan",
            [_unit()],
            _skill_spec(),
            collection_id="collection-plan",
            source_manifests=[_source_manifest()],
            unresolved_conflicts=[],
            runtime_product=_product(),
        )
    assert publish_error.value.code == ErrorCode.RUNTIME_CLOSURE_INVALID
