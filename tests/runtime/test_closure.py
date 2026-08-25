"""B2S-M13-01: Runtime Closure isolation Spike tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import validate as validate_jsonschema
from pydantic import ValidationError

from book2skill.runtime.closure import (
    CapabilityKind,
    ClosureResource,
    ProvenanceRef,
    ResourceClass,
    RuntimeClosureError,
    RuntimeClosureManifest,
    RuntimeClosureSession,
    ToolCapability,
    validate_closure,
    write_manifest,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_closure(
    root: Path,
    *,
    lifecycle: str = "production",
    pack_version: str = "1.0.0",
    include_optional: bool = False,
) -> RuntimeClosureManifest:
    (root / "packs" / "plan" / pack_version).mkdir(parents=True)
    (root / "schemas").mkdir()
    (root / "tools").mkdir()
    (root / "task").mkdir()
    (root / "kernel.md").write_text(
        "# Plan kernel\n\nFollow the approved planning sequence.\n",
        encoding="utf-8",
    )
    (root / "packs" / "plan" / pack_version / "pack.json").write_text(
        json.dumps(
            {"pack_id": "plan", "version": pack_version, "approved": True},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "schemas" / "io.json").write_text(
        '{"schema_version": 1, "input": "task"}\n', encoding="utf-8"
    )
    (root / "tools" / "emit.py").write_text(
        "from pathlib import Path\n"
        "import json\n"
        "root = Path(__file__).resolve().parents[1]\n"
        "payload = json.loads((root / 'task' / 'input.json').read_text())\n"
        "closure = json.loads((root / 'runtime-closure.json').read_text())\n"
        "result_payload = {'plan': payload['task'],\n"
        "    'runtime_closure_hash': closure['closure_hash']}\n"
        "result = json.dumps(result_payload, sort_keys=True) + '\\n'\n"
        "(root / 'task' / 'output.json').write_text(result)\n",
        encoding="utf-8",
    )
    (root / "task" / "input.json").write_text(
        '{"task": "prepare-release"}\n', encoding="utf-8"
    )
    if include_optional:
        (root / "optional.md").write_text("Optional host note.\n", encoding="utf-8")

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
            path=f"packs/plan/{pack_version}/pack.json",
            version=pack_version,
            sha256=_sha256(
                root / "packs" / "plan" / pack_version / "pack.json"
            ),
            provenance=[
                ProvenanceRef(
                    source_id="fixture:plan",
                    locator=f"pack/{pack_version}/pack.json",
                )
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
    if include_optional:
        resources.append(
            ClosureResource(
                resource_id="ref:optional",
                kind="reference",
                resource_class=ResourceClass.OPTIONAL,
                path="optional.md",
                version="1.0.0",
                sha256=_sha256(root / "optional.md"),
                provenance=[
                    ProvenanceRef(source_id="fixture:plan", locator="optional.md")
                ],
            )
        )

    manifest = RuntimeClosureManifest(
        closure_version="1.0.0",
        lifecycle=lifecycle,
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        skill_kernel_id="kernel.plan",
        skill_kernel_version="1.0.0",
        asset_pack_id="pack.plan",
        asset_pack_version=pack_version,
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
            ),
            ToolCapability(
                capability_id="host:search",
                kind=CapabilityKind.HOST_OPTIONAL,
                version="1.0.0",
            ),
        ],
    ).with_hash()
    write_manifest(root, manifest)
    return manifest


def test_runtime_closure_validates_and_opens_a_frozen_session(tmp_path: Path) -> None:
    manifest = _make_closure(tmp_path)

    report = validate_closure(tmp_path, manifest)
    session = RuntimeClosureSession.open(tmp_path)

    assert report.closure_hash == manifest.closure_hash
    assert session.closure_hash == manifest.closure_hash
    assert session.resource_path("kernel:plan").name == "kernel.md"
    assert "host:search" in report.optional_capabilities_missing


def test_product_capability_binding_is_order_independent_and_strict(
    tmp_path: Path,
) -> None:
    manifest = _make_closure(tmp_path)
    session = RuntimeClosureSession.open(tmp_path)

    session.assert_capabilities(list(reversed(manifest.capabilities)))

    mismatched = [
        manifest.capabilities[0].model_copy(update={"version": "2.0.0"}),
        manifest.capabilities[1],
    ]
    with pytest.raises(RuntimeClosureError) as exc_info:
        session.assert_capabilities(mismatched)

    assert exc_info.value.code == "RUNTIME_CLOSURE_INVALID"
    assert exc_info.value.details["closure_capability_ids"] == [
        "tool:emit",
        "host:search",
    ]


def test_manifest_matches_versioned_json_schema(tmp_path: Path) -> None:
    manifest = _make_closure(tmp_path)
    schema = json.loads(
        (
            Path(__file__).parents[2] / "schemas" / "runtime-closure.schema.json"
        ).read_text(encoding="utf-8")
    )

    validate_jsonschema(manifest.model_dump(mode="json"), schema)


def test_manifest_rejects_duplicate_resource_paths(tmp_path: Path) -> None:
    manifest = _make_closure(tmp_path)
    payload = manifest.model_dump(mode="json")
    duplicate = dict(payload["resources"][0])
    duplicate["resource_id"] = "resource:duplicate"
    payload["resources"].append(duplicate)

    with pytest.raises(ValidationError, match="resource path"):
        RuntimeClosureManifest.model_validate(payload)


def test_provenance_source_hash_is_validated() -> None:
    with pytest.raises(ValidationError):
        ProvenanceRef(source_id="fixture", locator="p1", source_sha256="broken")


def test_resource_requires_non_empty_unique_provenance() -> None:
    with pytest.raises(ValidationError, match="require at least one provenance"):
        ClosureResource(
            resource_id="resource:empty",
            kind="reference",
            resource_class=ResourceClass.OPTIONAL,
            path="optional.md",
            version="1.0.0",
            sha256="0" * 64,
        )

    provenance = ProvenanceRef(source_id="fixture:plan", locator="p1")
    with pytest.raises(ValidationError, match="provenance references must be unique"):
        ClosureResource(
            resource_id="resource:duplicate",
            kind="reference",
            resource_class=ResourceClass.OPTIONAL,
            path="optional.md",
            version="1.0.0",
            sha256="0" * 64,
            provenance=[provenance, provenance],
        )


def test_missing_exact_resource_fails_closed(tmp_path: Path) -> None:
    manifest = _make_closure(tmp_path)
    (tmp_path / "kernel.md").unlink()

    with pytest.raises(RuntimeClosureError) as exc_info:
        validate_closure(tmp_path, manifest)

    assert exc_info.value.code == "RUNTIME_CLOSURE_RESOURCE_MISSING"
    assert exc_info.value.details["resource_id"] == "kernel:plan"


def test_resource_hash_tamper_fails_closed(tmp_path: Path) -> None:
    manifest = _make_closure(tmp_path)
    (tmp_path / "kernel.md").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(RuntimeClosureError) as exc_info:
        validate_closure(tmp_path, manifest)

    assert exc_info.value.code == "RUNTIME_CLOSURE_HASH_MISMATCH"


def test_resolved_version_mismatch_fails_closed(tmp_path: Path) -> None:
    manifest = _make_closure(tmp_path)

    with pytest.raises(RuntimeClosureError) as exc_info:
        validate_closure(
            tmp_path,
            manifest,
            resolved_versions={
                "kernel:plan": "1.0.0",
                "pack:plan": "2.0.0",
                "schema:io": "1.0.0",
                "tool:emit": "1.0.0",
            },
        )

    assert exc_info.value.code == "RUNTIME_CLOSURE_VERSION_MISMATCH"
    assert exc_info.value.details["expected"] == "1.0.0"


def test_optional_resource_can_be_absent_but_required_capability_cannot(
    tmp_path: Path,
) -> None:
    manifest = _make_closure(tmp_path, include_optional=True)
    (tmp_path / "optional.md").unlink()

    report = validate_closure(tmp_path, manifest)
    assert report.optional_resources_missing == ("ref:optional",)

    required_manifest = manifest.model_copy(
        update={
            "capabilities": [
                ToolCapability(
                    capability_id="host:required",
                    kind=CapabilityKind.HOST_REQUIRED,
                    version="1.0.0",
                )
            ]
        }
    ).with_hash()
    with pytest.raises(RuntimeClosureError) as exc_info:
        validate_closure(tmp_path, required_manifest)
    assert exc_info.value.code == "RUNTIME_CLOSURE_CAPABILITY_MISSING"


def test_candidate_cannot_enter_production_install(tmp_path: Path) -> None:
    manifest = _make_closure(tmp_path, lifecycle="candidate")

    with pytest.raises(RuntimeClosureError) as exc_info:
        validate_closure(tmp_path, manifest)

    assert exc_info.value.code == "RUNTIME_CLOSURE_CANDIDATE_NOT_PUBLISHED"


def test_closure_hash_is_frozen_when_active_pack_pointer_changes(
    tmp_path: Path,
) -> None:
    manifest = _make_closure(tmp_path, pack_version="1.0.0")
    session = RuntimeClosureSession.open(tmp_path)
    (tmp_path / "active-pack.json").write_text(
        '{"pack_id": "pack.plan", "version": "2.0.0"}\n', encoding="utf-8"
    )

    assert session.closure_hash == manifest.closure_hash
    assert session.resource_path("pack:plan").as_posix().endswith(
        "packs/plan/1.0.0/pack.json"
    )
    session.verify()


def test_same_kernel_supports_two_compatible_pack_versions(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _make_closure(first_root, pack_version="1.0.0")
    second = _make_closure(second_root, pack_version="1.1.0")

    assert first.task_contract_id == second.task_contract_id
    assert first.skill_kernel_id == second.skill_kernel_id
    assert first.io_schema_id == second.io_schema_id
    assert first.security_profile_id == second.security_profile_id
    assert first.resources[0].sha256 == second.resources[0].sha256
    assert first.asset_pack_version == "1.0.0"
    assert second.asset_pack_version == "1.1.0"
    assert first.closure_hash != second.closure_hash


def test_bundled_tool_runs_without_core_or_source_tree(tmp_path: Path) -> None:
    manifest = _make_closure(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"

    result = subprocess.run(
        [sys.executable, str(tmp_path / "tools" / "emit.py")],
        cwd=outside,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == ""
    assert json.loads((tmp_path / "task" / "output.json").read_text()) == {
        "plan": "prepare-release",
        "runtime_closure_hash": manifest.closure_hash,
    }
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".json", ".py"}:
            assert "book2skill" not in path.read_text(encoding="utf-8").lower()


def test_project_host_install_runs_from_only_the_closure(tmp_path: Path) -> None:
    built = tmp_path / "built"
    built.mkdir()
    manifest = _make_closure(built)
    project = tmp_path / "project"
    skill = project / ".skills" / "plan"
    skill.mkdir(parents=True)
    for relative in ("kernel.md", "runtime-closure.json"):
        shutil.copy2(built / relative, skill / relative)
    for directory in ("packs", "schemas", "tools", "task"):
        shutil.copytree(built / directory, skill / directory)
    # These project-level paths model hidden build/source inputs.  The closure
    # task must not resolve anything from them.
    for directory in ("Core", "source", "workspace", "output"):
        (project / directory).mkdir()
    (project / "source" / "original.txt").write_text("hidden\n", encoding="utf-8")
    (project / "workspace" / "raw").mkdir()
    (project / "output" / "bundles").mkdir(parents=True)

    session = RuntimeClosureSession.open(skill)
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

    assert json.loads((skill / "task" / "output.json").read_text()) == {
        "plan": "prepare-release",
        "runtime_closure_hash": manifest.closure_hash,
    }
    assert not (skill / "Core").exists()
    assert not (skill / "source").exists()


@pytest.mark.parametrize("path", ["/absolute/file", "C:/absolute/file", "../escape"])
def test_resource_paths_are_relative_and_traversal_free(path: str) -> None:
    with pytest.raises(ValidationError):
        ClosureResource(
            resource_id="bad",
            kind="kernel",
            resource_class=ResourceClass.EXACT_REQUIRED,
            path=path,
            version="1.0.0",
            sha256="0" * 64,
        )
