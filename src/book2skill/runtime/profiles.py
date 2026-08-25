"""Generated Skill product profiles, host preflight and task fan-out.

The contracts in this module are deliberately independent of the Build and
HostInstaller implementations.  They make the product boundary explicit:
Standalone products carry their runtime closure, while Extension-backed
products must declare a Core/SDK dependency before a host can install them.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.runtime.closure import (
    CapabilityKind,
    ProvenanceRef,
    ToolCapability,
)

_SHA256_PATTERN = r"^[a-f0-9]{64}$"


class ProductProfile(StrEnum):
    """Runtime distribution profile for one generated Skill product."""

    STANDALONE = "standalone"
    EXTENSION_BACKED = "extension_backed"


class CoreRuntimeDependency(BaseModel):
    """Explicit Core and public SDK versions required by an Extension."""

    model_config = ConfigDict(extra="forbid")

    core_id: str = Field(default="book2skill.core", min_length=1)
    core_version: str = Field(..., min_length=1)
    sdk_id: str = Field(default="book2skill.sdk", min_length=1)
    sdk_version: str = Field(..., min_length=1)
    permissions: tuple[str, ...] = ()


class HostRuntime(BaseModel):
    """Capabilities visible to a product install preflight."""

    model_config = ConfigDict(extra="forbid")

    core_id: str = Field(default="book2skill.core", min_length=1)
    core_version: str | None = None
    sdk_id: str = Field(default="book2skill.sdk", min_length=1)
    sdk_version: str | None = None
    permissions: tuple[str, ...] = ()
    capabilities: dict[str, str] = Field(default_factory=dict)


class PreflightReport(BaseModel):
    """Machine-readable result of a host capability and dependency check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: ProductProfile
    installable: bool
    degraded: bool = False
    missing_runtime_dependencies: tuple[str, ...] = ()
    runtime_version_mismatches: tuple[str, ...] = ()
    missing_permissions: tuple[str, ...] = ()
    required_capabilities_missing: tuple[str, ...] = ()
    required_capability_version_mismatches: tuple[str, ...] = ()
    optional_capabilities_missing: tuple[str, ...] = ()
    optional_capability_version_mismatches: tuple[str, ...] = ()


class ProductPreflightError(DomainError):
    """Stable fail-closed error raised by ``assert_installable``."""

    def __init__(
        self,
        code: ErrorCode,
        product_id: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code,
            product_id,
            message,
            recovery=(
                "Install the declared dependency or select a compatible "
                "product profile."
            ),
            details=details,
        )


class GeneratedSkillProduct(BaseModel):
    """Task-centered product descriptor used by install preflight."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    product_id: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    task_contract_id: str = Field(..., min_length=1)
    task_contract_version: str = Field(..., min_length=1)
    profile: ProductProfile
    runtime_dependency: CoreRuntimeDependency | None = None
    capabilities: list[ToolCapability] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_profile_dependency(self) -> Self:
        if self.profile == ProductProfile.STANDALONE and self.runtime_dependency:
            raise ValueError(
                "PROFILE_INVALID: Standalone products cannot declare a runtime "
                "Core dependency"
            )
        if (
            self.profile == ProductProfile.EXTENSION_BACKED
            and self.runtime_dependency is None
        ):
            raise ValueError(
                "PROFILE_INVALID: Extension-backed products must declare Core "
                "and SDK versions"
            )
        capability_ids = [item.capability_id for item in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("PROFILE_INVALID: capability_id values must be unique")
        return self

    def preflight(self, host: HostRuntime) -> PreflightReport:
        """Check this product against a host without importing Core."""

        missing_dependencies: list[str] = []
        dependency_mismatches: list[str] = []
        missing_permissions: list[str] = []
        if self.profile == ProductProfile.EXTENSION_BACKED:
            dependency = self.runtime_dependency
            assert dependency is not None
            for package_id, expected_version, host_id, host_version in (
                (
                    dependency.core_id,
                    dependency.core_version,
                    host.core_id,
                    host.core_version,
                ),
                (
                    dependency.sdk_id,
                    dependency.sdk_version,
                    host.sdk_id,
                    host.sdk_version,
                ),
            ):
                if host_id != package_id or host_version is None:
                    missing_dependencies.append(package_id)
                elif host_version != expected_version:
                    dependency_mismatches.append(package_id)
            missing_permissions.extend(
                permission
                for permission in dependency.permissions
                if permission not in host.permissions
            )

        required_missing: list[str] = []
        required_mismatches: list[str] = []
        optional_missing: list[str] = []
        optional_mismatches: list[str] = []
        for capability in self.capabilities:
            if capability.kind == CapabilityKind.BUNDLED_SCRIPT:
                continue
            is_optional = capability.kind == CapabilityKind.HOST_OPTIONAL
            available_version = host.capabilities.get(capability.capability_id)
            if capability.kind == CapabilityKind.UNSUPPORTED:
                available_version = None
            if available_version is None:
                (optional_missing if is_optional else required_missing).append(
                    capability.capability_id
                )
            elif available_version != capability.version:
                (optional_mismatches if is_optional else required_mismatches).append(
                    capability.capability_id
                )
                (optional_missing if is_optional else required_missing).append(
                    capability.capability_id
                )

        return PreflightReport(
            profile=self.profile,
            installable=not (
                missing_dependencies
                or dependency_mismatches
                or missing_permissions
                or required_missing
            ),
            degraded=bool(optional_missing),
            missing_runtime_dependencies=tuple(missing_dependencies),
            runtime_version_mismatches=tuple(dependency_mismatches),
            missing_permissions=tuple(missing_permissions),
            required_capabilities_missing=tuple(required_missing),
            required_capability_version_mismatches=tuple(required_mismatches),
            optional_capabilities_missing=tuple(optional_missing),
            optional_capability_version_mismatches=tuple(optional_mismatches),
        )

    def assert_installable(self, host: HostRuntime) -> PreflightReport:
        """Return evidence or raise a stable fail-closed preflight error."""

        report = self.preflight(host)
        if report.missing_runtime_dependencies:
            raise ProductPreflightError(
                ErrorCode.PROFILE_CORE_REQUIRED,
                self.product_id,
                "Extension-backed product requires an unavailable Core or SDK",
                details={
                    "missing_runtime_dependencies": report.missing_runtime_dependencies
                },
            )
        if report.runtime_version_mismatches:
            raise ProductPreflightError(
                ErrorCode.PROFILE_CORE_VERSION_MISMATCH,
                self.product_id,
                "Host Core or SDK version is incompatible with the product",
                details={
                    "runtime_version_mismatches": report.runtime_version_mismatches
                },
            )
        if report.missing_permissions:
            raise ProductPreflightError(
                ErrorCode.PROFILE_PERMISSION_MISSING,
                self.product_id,
                "Host has not granted a permission declared by the product",
                details={"missing_permissions": report.missing_permissions},
            )
        if report.required_capabilities_missing:
            raise ProductPreflightError(
                ErrorCode.PROFILE_CAPABILITY_MISSING,
                self.product_id,
                "Host does not provide a required product capability",
                details={
                    "required_capabilities_missing": (
                        report.required_capabilities_missing
                    )
                },
            )
        return report


class TaskCandidate(BaseModel):
    """One independent task product candidate from a shared source."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    task_id: str = Field(..., min_length=1)
    product_id: str = Field(..., min_length=1)
    task_contract_id: str = Field(..., min_length=1)
    task_contract_version: str = Field(..., min_length=1)
    skill_kernel_id: str = Field(..., min_length=1)
    skill_kernel_version: str = Field(..., min_length=1)
    profile: ProductProfile
    provenance: list[ProvenanceRef] = Field(..., min_length=1)


class TaskFanOutPlan(BaseModel):
    """One-to-many task mapping retaining source provenance for every task."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    source_id: str = Field(..., min_length=1)
    source_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    tasks: tuple[TaskCandidate, ...] = Field(..., min_length=1)

    @property
    def product_ids(self) -> tuple[str, ...]:
        return tuple(task.product_id for task in self.tasks)

    @model_validator(mode="after")
    def _validate_unique_tasks_and_source(self) -> Self:
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("FANOUT_DUPLICATE_TASK: task_id values must be unique")
        product_ids = [task.product_id for task in self.tasks]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("FANOUT_INVALID: product_id values must be unique")
        for task in self.tasks:
            for reference in task.provenance:
                if reference.source_id != self.source_id:
                    raise ValueError(
                        "FANOUT_INVALID: task provenance must point to the fan-out "
                        "source"
                    )
                if (
                    self.source_sha256 is not None
                    and reference.source_sha256 is not None
                    and reference.source_sha256 != self.source_sha256
                ):
                    raise ValueError(
                        "FANOUT_INVALID: task provenance source hash does not match "
                        "the source"
                    )
        return self


class FanOutError(DomainError):
    """Stable error raised before fan-out products are materialized."""

    def __init__(
        self,
        code: ErrorCode,
        source_id: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code,
            source_id,
            message,
            recovery="Give each task a distinct identity and retain source locators.",
            details=details,
        )


class TaskFanOutCompiler:
    """Validate and freeze a source-to-independent-task mapping."""

    def plan(
        self,
        *,
        source_id: str,
        tasks: Iterable[TaskCandidate],
        source_sha256: str | None = None,
    ) -> TaskFanOutPlan:
        if not source_id.strip():
            raise FanOutError(
                ErrorCode.FANOUT_INVALID,
                source_id,
                "Fan-out source_id must not be empty",
            )
        task_list = list(tasks)
        if not task_list:
            raise FanOutError(
                ErrorCode.FANOUT_INVALID,
                source_id,
                "A fan-out plan requires at least one task",
            )
        task_ids = [task.task_id for task in task_list]
        duplicate_ids = sorted(
            {task_id for task_id in task_ids if task_ids.count(task_id) > 1}
        )
        if duplicate_ids:
            raise FanOutError(
                ErrorCode.FANOUT_DUPLICATE_TASK,
                source_id,
                "A source cannot fan out to the same task identity twice",
                details={"task_ids": duplicate_ids},
            )
        product_ids = [task.product_id for task in task_list]
        if len(product_ids) != len(set(product_ids)):
            raise FanOutError(
                ErrorCode.FANOUT_INVALID,
                source_id,
                "Fan-out product_id values must be unique",
            )
        for task in task_list:
            for reference in task.provenance:
                if reference.source_id != source_id:
                    raise FanOutError(
                        ErrorCode.FANOUT_INVALID,
                        source_id,
                        "Task provenance must point to the fan-out source",
                        details={"task_id": task.task_id},
                    )
                if (
                    source_sha256 is not None
                    and reference.source_sha256 is not None
                    and reference.source_sha256 != source_sha256
                ):
                    raise FanOutError(
                        ErrorCode.FANOUT_INVALID,
                        source_id,
                        "Task provenance source hash does not match the fan-out source",
                        details={"task_id": task.task_id},
                    )
        return TaskFanOutPlan(
            source_id=source_id,
            source_sha256=source_sha256,
            tasks=tuple(task_list),
        )


__all__ = [
    "CoreRuntimeDependency",
    "FanOutError",
    "GeneratedSkillProduct",
    "HostRuntime",
    "PreflightReport",
    "ProductPreflightError",
    "ProductProfile",
    "TaskCandidate",
    "TaskFanOutCompiler",
    "TaskFanOutPlan",
]
