"""B2S-M13-06: Legacy audit, Closure gate and host matrix regression."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import book2skill.runtime.migration as migration_module
from book2skill.domain import SourceFormat, SourceManifest
from book2skill.domain.errors import ErrorCode
from book2skill.hosts.registry import HOST_KINDS, get_installer
from book2skill.runtime.closure import (
    CapabilityKind,
    ClosureLifecycle,
    ClosureResource,
    ProvenanceRef,
    ResourceClass,
    RuntimeClosureError,
    RuntimeClosureManifest,
    RuntimeClosureSession,
    ToolCapability,
    write_manifest,
)
from book2skill.runtime.migration import (
    LegacyMigrationAuditor,
    LegacyMigrator,
    MigrationDisposition,
    MigrationError,
)
from book2skill.runtime.product_manifest import RuntimeProductManifest
from book2skill.runtime.profiles import GeneratedSkillProduct, HostRuntime


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_closure(
    root: Path, *, lifecycle: ClosureLifecycle = ClosureLifecycle.PRODUCTION
) -> RuntimeClosureManifest:
    (root / "packs" / "plan" / "1.0.0").mkdir(parents=True)
    (root / "schemas").mkdir()
    (root / "tools").mkdir()
    (root / "task").mkdir()
    (root / "kernel.md").write_text(
        "# Plan kernel\n\nFollow the approved planning sequence.\n",
        encoding="utf-8",
    )
    (root / "packs" / "plan" / "1.0.0" / "pack.json").write_text(
        '{"pack_id": "plan", "version": "1.0.0"}\n', encoding="utf-8"
    )
    (root / "schemas" / "io.json").write_text(
        '{"schema_version": 1, "input": "task"}\n', encoding="utf-8"
    )
    (root / "tools" / "emit.py").write_text(
        "from pathlib import Path\n"
        "import json\n"
        "root = Path(__file__).resolve().parents[1]\n"
        "closure = json.loads((root / 'runtime-closure.json').read_text())\n"
        "payload = {'runtime_closure_hash': closure['closure_hash']}\n"
        "(root / 'task' / 'output.json').write_text(\n"
        "    json.dumps(payload, sort_keys=True) + '\\n'\n"
        ")\n",
        encoding="utf-8",
    )
    (root / "task" / "input.json").write_text('{"task": "plan"}\n', encoding="utf-8")
    (root / "SKILL.md").write_text(
        "---\nname: plan-skill\ndescription: A portable plan fixture.\n---\n"
        "# Plan\n",
        encoding="utf-8",
    )
    resources = [
        ClosureResource(
            resource_id="kernel:plan",
            kind="kernel",
            resource_class=ResourceClass.EXACT_REQUIRED,
            path="kernel.md",
            version="1.0.0",
            sha256=_sha256(root / "kernel.md"),
            provenance=[ProvenanceRef(source_id="fixture:plan", locator="kernel.md")],
        ),
        ClosureResource(
            resource_id="pack:plan",
            kind="asset_pack",
            resource_class=ResourceClass.SEMANTIC_RETRIEVAL,
            path="packs/plan/1.0.0/pack.json",
            version="1.0.0",
            sha256=_sha256(root / "packs" / "plan" / "1.0.0" / "pack.json"),
            provenance=[
                ProvenanceRef(source_id="fixture:plan", locator="pack/1.0.0")
            ],
        ),
        ClosureResource(
            resource_id="schema:io",
            kind="schema",
            resource_class=ResourceClass.EXACT_REQUIRED,
            path="schemas/io.json",
            version="1.0.0",
            sha256=_sha256(root / "schemas" / "io.json"),
            provenance=[ProvenanceRef(source_id="fixture:plan", locator="schema/io")],
        ),
        ClosureResource(
            resource_id="tool:emit",
            kind="tool",
            resource_class=ResourceClass.EXACT_REQUIRED,
            path="tools/emit.py",
            version="1.0.0",
            sha256=_sha256(root / "tools" / "emit.py"),
            provenance=[ProvenanceRef(source_id="fixture:plan", locator="tool/emit")],
        ),
    ]
    manifest = RuntimeClosureManifest(
        closure_version="1.0.0",
        lifecycle=lifecycle,
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        skill_kernel_id="kernel.plan",
        skill_kernel_version="1.0.0",
        asset_pack_id="pack.plan",
        asset_pack_version="1.0.0",
        io_schema_id="io.plan",
        io_schema_version="1.0.0",
        security_profile_id="standalone-default",
        security_profile_version="1.0.0",
        resources=resources,
        capabilities=[
            ToolCapability(
                capability_id="tool:emit",
                kind=CapabilityKind.BUNDLED_SCRIPT,
                version="1.0.0",
                resource_id="tool:emit",
            )
        ],
    ).with_hash()
    write_manifest(root, manifest)
    return manifest


def test_legacy_audit_is_read_only_and_requires_explicit_migration(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy-plan"
    legacy.mkdir()
    (legacy / "SKILL.md").write_text("legacy\n", encoding="utf-8")
    before = _sha256(legacy / "SKILL.md")

    auditor = LegacyMigrationAuditor()
    assessment = auditor.inspect(legacy)
    plan = auditor.plan([legacy])

    assert assessment.disposition == MigrationDisposition.LEGACY_UNCLOSED
    assert plan.auto_migrate is False
    assert plan.manual_approval_required is True
    assert _sha256(legacy / "SKILL.md") == before
    with pytest.raises(MigrationError) as exc_info:
        auditor.require_manual_approval(plan)
    assert exc_info.value.code == ErrorCode.MIGRATION_APPROVAL_REQUIRED


def test_production_closure_is_migration_ready(tmp_path: Path) -> None:
    root = tmp_path / "ready"
    manifest = _make_closure(root)

    assessment = LegacyMigrationAuditor().inspect(root)

    assert assessment.disposition == MigrationDisposition.CLOSURE_READY
    assert assessment.closure_hash == manifest.closure_hash
    assert assessment.reason_codes == ()


def test_approved_legacy_batch_migration_stages_backs_up_and_publishes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "legacy" / "plan-skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: plan-skill\ndescription: A migrated plan skill.\n---\n# Plan\n",
        encoding="utf-8",
    )
    source = SourceManifest(
        source_id="source-plan",
        version=1,
        original_name="plan.txt",
        content_sha256="a" * 64,
        format=SourceFormat.TXT,
        ingested_at="2026-08-25T00:00:00+00:00",
    )
    (root / "provenance.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "skill_name": "plan-skill",
                "sources": [source.model_dump(mode="json")],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "references" / "provenance.md").write_text(
        "# Provenance\n\n- source-plan / source-plan-c1\n", encoding="utf-8"
    )
    before = (root / "SKILL.md").read_bytes()

    with pytest.raises(MigrationError) as approval_error:
        LegacyMigrator().migrate(
            [root], approved_skill_ids=[], backup_root=tmp_path / "backups"
        )
    assert approval_error.value.code == ErrorCode.MIGRATION_APPROVAL_REQUIRED
    assert (root / "SKILL.md").read_bytes() == before

    results = LegacyMigrator().migrate(
        [root],
        approved_skill_ids=["plan-skill"],
        backup_root=tmp_path / "backups",
        confirm=True,
    )
    assert results[0].migrated is True
    assert results[0].backup_path is not None
    assert results[0].backup_path.exists()
    assert (results[0].backup_path / "SKILL.md").read_bytes() == before
    descriptor = RuntimeProductManifest.from_file(root / "runtime-product.json")
    assert descriptor.closure_hash == results[0].closure_hash
    session = RuntimeClosureSession.open(root)
    assert session.manifest.lifecycle == ClosureLifecycle.PRODUCTION
    assert all(
        reference.locator == "block:source-plan-c1"
        for resource in session.manifest.resources
        for reference in resource.provenance
    )


def test_batch_activation_failure_restores_already_committed_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots: list[Path] = []
    for name, source_id in (
        ("first-skill", "source-first"),
        ("second-skill", "source-second"),
    ):
        root = tmp_path / "skills" / name
        (root / "references").mkdir(parents=True)
        (root / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A rollback fixture.\n---\n# Skill\n",
            encoding="utf-8",
        )
        source = SourceManifest(
            source_id=source_id,
            version=1,
            original_name=f"{name}.txt",
            content_sha256="b" * 64,
            format=SourceFormat.TXT,
            ingested_at="2026-08-25T00:00:00+00:00",
        )
        (root / "provenance.yml").write_text(
            yaml.safe_dump(
                {"schema_version": 1, "sources": [source.model_dump(mode="json")]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (root / "references" / "provenance.md").write_text(
            f"- {source_id} / {source_id}-c1\n", encoding="utf-8"
        )
        roots.append(root)
    originals = {root.name: (root / "SKILL.md").read_bytes() for root in roots}

    real_replace = migration_module.os.replace

    def fail_second_activation(
        source: str | os.PathLike[str], target: str | os.PathLike[str]
    ) -> None:
        if (
            ".second-skill.migration-" in str(source)
            and Path(target).resolve() == roots[1].resolve()
        ):
            raise OSError("injected activation failure")
        real_replace(source, target)

    monkeypatch.setattr(migration_module.os, "replace", fail_second_activation)
    with pytest.raises(MigrationError) as exc_info:
        LegacyMigrator().migrate(
            roots,
            approved_skill_ids=[root.name for root in roots],
            backup_root=tmp_path / "backups",
            confirm=True,
        )
    assert exc_info.value.code == ErrorCode.MIGRATION_INVALID
    for root in roots:
        assert (root / "SKILL.md").read_bytes() == originals[root.name]
        assert not (root / "runtime-product.json").exists()


@pytest.mark.parametrize("lifecycle", [ClosureLifecycle.CANDIDATE])
def test_candidate_closure_is_blocked_from_legacy_migration(
    tmp_path: Path, lifecycle: ClosureLifecycle
) -> None:
    root = tmp_path / "candidate"
    _make_closure(root, lifecycle=lifecycle)

    assessment = LegacyMigrationAuditor().inspect(root)

    assert assessment.disposition == MigrationDisposition.BLOCKED
    assert assessment.reason_codes == ("RUNTIME_CLOSURE_CANDIDATE_NOT_PUBLISHED",)


def test_corrupt_closure_is_blocked_without_leaking_path_or_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corrupt"
    _make_closure(root)
    (root / "kernel.md").write_text("tampered\n", encoding="utf-8")

    assessment = LegacyMigrationAuditor().inspect(root)

    assert assessment.disposition == MigrationDisposition.BLOCKED
    assert assessment.reason_codes == ("RUNTIME_CLOSURE_HASH_MISMATCH",)
    serialized = assessment.model_dump_json()
    assert str(root) not in serialized
    assert "tampered" not in serialized


def test_all_supported_project_hosts_install_and_reopen_the_same_closure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    manifest = _make_closure(source)
    product = GeneratedSkillProduct(
        product_id="skill.plan",
        task_id="plan",
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        profile="standalone",
        capabilities=[
            ToolCapability(
                capability_id="tool:emit",
                kind=CapabilityKind.BUNDLED_SCRIPT,
                version="1.0.0",
                resource_id="tool:emit",
            )
        ],
    )

    assert product.assert_installable(HostRuntime()).installable is True
    for host in HOST_KINDS:
        project = tmp_path / "hosts" / host
        installer = get_installer(
            host,
            project_level=True,
            project_root=project,
            backup_root=tmp_path / "backups" / host,
        )
        record = installer.install_runtime_product(
            source,
            product,
            HostRuntime(),
            expected_closure_hash=manifest.closure_hash,
        )
        session_root = record.target_dir
        session = RuntimeClosureSession.open(session_root)
        assert f"runtime_closure_hash={manifest.closure_hash}" in record.notes
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONNOUSERSITE"] = "1"
        subprocess.run(
            [sys.executable, str(session.resource_path("tool:emit"))],
            cwd=project,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        output = json.loads((session_root / "task" / "output.json").read_text())
        assert output == {"runtime_closure_hash": manifest.closure_hash}


def test_runtime_install_gate_rejects_candidate_before_host_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "candidate"
    manifest = _make_closure(source, lifecycle=ClosureLifecycle.CANDIDATE)
    project = tmp_path / "project"
    installer = get_installer(
        "project",
        project_level=True,
        project_root=project,
        backup_root=tmp_path / "backups",
    )
    product = GeneratedSkillProduct(
        product_id="skill.plan",
        task_id="plan",
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        profile="standalone",
    )

    with pytest.raises(RuntimeClosureError) as exc_info:
        installer.install_runtime_product(
            source,
            product,
            HostRuntime(),
            expected_closure_hash=manifest.closure_hash,
        )

    assert exc_info.value.code == ErrorCode.RUNTIME_CLOSURE_CANDIDATE_NOT_PUBLISHED
    assert not (project / "skills" / "plan-skill").exists()


def test_migration_plan_rejects_duplicate_skill_identity(tmp_path: Path) -> None:
    first = tmp_path / "same"
    second = tmp_path / "nested" / "same"
    first.mkdir()
    second.mkdir(parents=True)

    with pytest.raises(MigrationError) as exc_info:
        LegacyMigrationAuditor().plan([first, second])

    assert exc_info.value.code == ErrorCode.MIGRATION_INVALID
