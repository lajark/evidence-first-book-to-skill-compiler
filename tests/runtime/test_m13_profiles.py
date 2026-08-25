"""B2S-M13-05: product profiles, host preflight and task fan-out."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from book2skill.domain.errors import ErrorCode
from book2skill.runtime.closure import CapabilityKind, ProvenanceRef, ToolCapability
from book2skill.runtime.profiles import (
    CoreRuntimeDependency,
    FanOutError,
    GeneratedSkillProduct,
    HostRuntime,
    ProductProfile,
    TaskCandidate,
    TaskFanOutCompiler,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _provenance(source_id: str, locator: str = "p10") -> ProvenanceRef:
    return ProvenanceRef(
        source_id=source_id,
        locator=locator,
        source_sha256=_hash(source_id),
    )


def _task(task_id: str, source_id: str = "book-a") -> TaskCandidate:
    return TaskCandidate(
        task_id=task_id,
        product_id=f"skill.{task_id}",
        task_contract_id=f"task.{task_id}",
        task_contract_version="1.0.0",
        skill_kernel_id="kernel.plan",
        skill_kernel_version="1.0.0",
        profile=ProductProfile.STANDALONE,
        provenance=[_provenance(source_id, f"{task_id}:p10")],
    )


def _standalone() -> GeneratedSkillProduct:
    return GeneratedSkillProduct(
        product_id="skill.plan",
        task_id="plan",
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        profile=ProductProfile.STANDALONE,
        capabilities=[
            ToolCapability(
                capability_id="tool.render",
                kind=CapabilityKind.BUNDLED_SCRIPT,
                version="1.0.0",
                resource_id="tool.render",
            )
        ],
    )


def _extension() -> GeneratedSkillProduct:
    return GeneratedSkillProduct(
        product_id="skill.plan.ext",
        task_id="plan",
        task_contract_id="task.plan",
        task_contract_version="1.0.0",
        profile=ProductProfile.EXTENSION_BACKED,
        runtime_dependency=CoreRuntimeDependency(
            core_id="book2skill.core",
            core_version="1.0.5",
            sdk_id="book2skill.sdk",
            sdk_version="1.0.0",
            permissions=("workspace.read",),
        ),
    )


def test_one_book_fans_out_to_independent_task_products() -> None:
    plan = TaskFanOutCompiler().plan(
        source_id="book-a",
        source_sha256=_hash("book-a"),
        tasks=[_task("summarize"), _task("quiz"), _task("coach")],
    )

    assert plan.source_id == "book-a"
    assert plan.product_ids == ("skill.summarize", "skill.quiz", "skill.coach")
    assert {task.task_id for task in plan.tasks} == {"summarize", "quiz", "coach"}
    assert all(
        ref.source_id == "book-a"
        for task in plan.tasks
        for ref in task.provenance
    )


def test_duplicate_task_id_is_rejected_without_collapsing_products() -> None:
    with pytest.raises(FanOutError) as exc_info:
        TaskFanOutCompiler().plan(
            source_id="book-a",
            tasks=[_task("summarize"), _task("summarize")],
        )

    assert exc_info.value.code == ErrorCode.FANOUT_DUPLICATE_TASK


def test_standalone_preflight_does_not_require_core() -> None:
    report = _standalone().preflight(HostRuntime())

    assert report.installable is True
    assert report.profile == ProductProfile.STANDALONE
    assert report.missing_runtime_dependencies == ()


def test_extension_preflight_rejects_missing_core_and_sdk() -> None:
    product = _extension()
    report = product.preflight(HostRuntime())

    assert report.installable is False
    assert report.missing_runtime_dependencies == (
        "book2skill.core",
        "book2skill.sdk",
    )
    with pytest.raises(Exception) as exc_info:
        product.assert_installable(HostRuntime())
    assert exc_info.value.code == ErrorCode.PROFILE_CORE_REQUIRED


def test_extension_preflight_rejects_incompatible_core_version() -> None:
    product = _extension()
    host = HostRuntime(
        core_id="book2skill.core",
        core_version="1.0.3",
        sdk_id="book2skill.sdk",
        sdk_version="1.0.0",
    )

    report = product.preflight(host)

    assert report.installable is False
    assert report.runtime_version_mismatches == ("book2skill.core",)
    with pytest.raises(Exception) as exc_info:
        product.assert_installable(host)
    assert exc_info.value.code == ErrorCode.PROFILE_CORE_VERSION_MISMATCH


def test_extension_preflight_rejects_missing_declared_permission() -> None:
    product = _extension()
    host = HostRuntime(
        core_id="book2skill.core",
        core_version="1.0.5",
        sdk_id="book2skill.sdk",
        sdk_version="1.0.0",
    )

    report = product.preflight(host)

    assert report.installable is False
    assert report.missing_permissions == ("workspace.read",)
    with pytest.raises(Exception) as exc_info:
        product.assert_installable(host)
    assert exc_info.value.code == ErrorCode.PROFILE_PERMISSION_MISSING


def test_required_capability_blocks_but_optional_capability_degrades() -> None:
    product = _standalone().model_copy(
        update={
            "capabilities": [
                ToolCapability(
                    capability_id="host.search",
                    kind=CapabilityKind.HOST_REQUIRED,
                    version="2.0.0",
                ),
                ToolCapability(
                    capability_id="host.telemetry",
                    kind=CapabilityKind.HOST_OPTIONAL,
                    version="1.0.0",
                ),
            ]
        }
    )

    report = product.preflight(HostRuntime(capabilities={"host.search": "1.0.0"}))

    assert report.installable is False
    assert report.required_capabilities_missing == ("host.search",)
    assert report.optional_capabilities_missing == ("host.telemetry",)
    assert report.degraded is True
    with pytest.raises(Exception) as exc_info:
        product.assert_installable(HostRuntime())
    assert exc_info.value.code == ErrorCode.PROFILE_CAPABILITY_MISSING


def test_extension_profile_must_declare_runtime_dependency() -> None:
    with pytest.raises(ValidationError, match="PROFILE_INVALID"):
        GeneratedSkillProduct(
            product_id="skill.invalid",
            task_id="invalid",
            task_contract_id="task.invalid",
            task_contract_version="1.0.0",
            profile=ProductProfile.EXTENSION_BACKED,
        )


def test_standalone_profile_cannot_carry_runtime_core_dependency() -> None:
    with pytest.raises(ValidationError, match="PROFILE_INVALID"):
        GeneratedSkillProduct(
            product_id="skill.invalid",
            task_id="invalid",
            task_contract_id="task.invalid",
            task_contract_version="1.0.0",
            profile=ProductProfile.STANDALONE,
            runtime_dependency=CoreRuntimeDependency(
                core_version="1.0.5",
                sdk_version="1.0.0",
            ),
        )


def test_extension_preflight_passes_with_exact_core_sdk_and_capabilities() -> None:
    product = _extension().model_copy(
        update={
            "capabilities": [
                ToolCapability(
                    capability_id="host.search",
                    kind=CapabilityKind.HOST_REQUIRED,
                    version="2.0.0",
                )
            ]
        }
    )
    host = HostRuntime(
        core_id="book2skill.core",
                core_version="1.0.5",
        sdk_id="book2skill.sdk",
        sdk_version="1.0.0",
        permissions=("workspace.read",),
        capabilities={"host.search": "2.0.0"},
    )

    assert product.preflight(host).installable is True
