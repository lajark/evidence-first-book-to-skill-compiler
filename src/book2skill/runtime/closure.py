"""Minimal Runtime Closure contract and fail-closed validator.

This module is intentionally small and host-agnostic.  It proves the M13
invariant on one product slice: a generated Skill can pin its kernel, one
approved Asset Pack, schemas and deterministic tools without consulting the
Core source tree or an active-pack pointer at runtime.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from book2skill.domain.errors import DomainError, ErrorCode

_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:")


class ResourceClass(StrEnum):
    """Runtime resource resolution class."""

    EXACT_REQUIRED = "exact_required"
    SEMANTIC_RETRIEVAL = "semantic_retrieval"
    OPTIONAL = "optional"


class CapabilityKind(StrEnum):
    """Where a runtime capability is supplied from."""

    BUNDLED_SCRIPT = "bundled_script"
    HOST_REQUIRED = "host_required"
    HOST_OPTIONAL = "host_optional"
    UNSUPPORTED = "unsupported"


class ClosureLifecycle(StrEnum):
    """Publication state accepted by the install gate."""

    CANDIDATE = "candidate"
    PRODUCTION = "production"


class ProvenanceRef(BaseModel):
    """Minimal source locator retained with a runtime resource."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., min_length=1)
    locator: str = Field(..., min_length=1)
    source_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class ClosureResource(BaseModel):
    """One pinned file in a Runtime Closure."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    resource_id: str = Field(..., min_length=1)
    kind: Literal["kernel", "asset_pack", "schema", "tool", "reference"]
    resource_class: ResourceClass
    path: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    sha256: str = Field(..., pattern=_SHA256_PATTERN)
    provenance: list[ProvenanceRef] = Field(
        default_factory=list,
        min_length=1,
        description="At least one source locator is required for every resource.",
    )

    @model_validator(mode="after")
    def _validate_unique_provenance(self) -> Self:
        if not self.provenance:
            raise ValueError(
                "Runtime Closure resources require at least one provenance reference"
            )
        keys = [
            (item.source_id, item.locator, item.source_sha256)
            for item in self.provenance
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("Runtime Closure provenance references must be unique")
        return self

    @field_validator("path")
    @classmethod
    def _validate_relative_path(cls, value: str) -> str:
        if "\\" in value or value.startswith("/") or _WINDOWS_ABSOLUTE.match(value):
            raise ValueError("Runtime Closure paths must be relative POSIX paths")
        parsed = PurePosixPath(value)
        if not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
            raise ValueError("Runtime Closure paths must not contain traversal parts")
        return value


class ToolCapability(BaseModel):
    """A deterministic bundled tool or host-provided capability."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    capability_id: str = Field(..., min_length=1)
    kind: CapabilityKind
    version: str = Field(..., min_length=1)
    resource_id: str | None = None

    @model_validator(mode="after")
    def _validate_bundled_resource(self) -> Self:
        if self.kind == CapabilityKind.BUNDLED_SCRIPT and not self.resource_id:
            raise ValueError("bundled_script capabilities require resource_id")
        return self


class RuntimeClosureManifest(BaseModel):
    """Versioned, hash-addressed manifest for one standalone Skill product."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    closure_version: str = Field(..., min_length=1)
    lifecycle: ClosureLifecycle
    task_contract_id: str = Field(..., min_length=1)
    task_contract_version: str = Field(..., min_length=1)
    skill_kernel_id: str = Field(..., min_length=1)
    skill_kernel_version: str = Field(..., min_length=1)
    asset_pack_id: str = Field(..., min_length=1)
    asset_pack_version: str = Field(..., min_length=1)
    asset_pack_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    io_schema_id: str = Field(..., min_length=1)
    io_schema_version: str = Field(..., min_length=1)
    security_profile_id: str = Field(..., min_length=1)
    security_profile_version: str = Field(..., min_length=1)
    resources: list[ClosureResource] = Field(..., min_length=1)
    capabilities: list[ToolCapability] = Field(default_factory=list)
    closure_hash: str | None = Field(None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> Self:
        resource_ids = [item.resource_id for item in self.resources]
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("Runtime Closure resource_id values must be unique")
        resource_paths = [item.path for item in self.resources]
        if len(resource_paths) != len(set(resource_paths)):
            raise ValueError("Runtime Closure resource path values must be unique")
        capability_ids = [item.capability_id for item in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("Runtime Closure capability_id values must be unique")
        return self

    def canonical_payload(self) -> dict[str, object]:
        """Return the hash input, excluding the self-referential hash field."""

        return self.model_dump(mode="json", exclude={"closure_hash"})

    def compute_hash(self) -> str:
        payload = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def with_hash(self) -> Self:
        return self.model_copy(update={"closure_hash": self.compute_hash()})


class RuntimeClosureError(DomainError):
    """Stable fail-closed error raised by Runtime Closure validation."""

    def __init__(
        self,
        code: ErrorCode,
        task_contract_id: str,
        message: str,
        *,
        recovery: str = "Regenerate or restore the pinned Runtime Closure.",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code,
            task_contract_id,
            message,
            recovery=recovery,
            details=details,
        )


@dataclass(frozen=True)
class ClosureValidationReport:
    """Evidence from one closure validation pass."""

    closure_hash: str
    verified_resource_ids: tuple[str, ...]
    optional_resources_missing: tuple[str, ...]
    optional_capabilities_missing: tuple[str, ...]


def _error(
    manifest: RuntimeClosureManifest,
    code: ErrorCode,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> RuntimeClosureError:
    return RuntimeClosureError(
        code,
        manifest.task_contract_id,
        message,
        details=details,
    )


def _safe_resource_path(root: Path, resource: ClosureResource) -> Path:
    root_resolved = root.resolve()
    candidate = (root / Path(*PurePosixPath(resource.path).parts)).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("Runtime Closure resource escapes its root") from exc
    return candidate


def _capability_resource_ids(manifest: RuntimeClosureManifest) -> set[str]:
    return {resource.resource_id for resource in manifest.resources}


def _capability_signature(
    capabilities: Iterable[ToolCapability],
) -> tuple[tuple[str, str, str, str], ...]:
    """Return an order-independent identity for capability declarations."""

    return tuple(
        sorted(
            (
                capability.capability_id,
                str(capability.kind),
                capability.version,
                capability.resource_id or "",
            )
            for capability in capabilities
        )
    )


def validate_closure(
    root: Path,
    manifest: RuntimeClosureManifest,
    *,
    required_lifecycle: ClosureLifecycle = ClosureLifecycle.PRODUCTION,
    resolved_versions: Mapping[str, str] | None = None,
    available_capabilities: Mapping[str, str] | None = None,
) -> ClosureValidationReport:
    """Validate all pinned resources and capability requirements.

    Missing or changed exact resources, version mismatches, required host
    capabilities and candidate manifests all fail closed.  Optional resources
    and host capabilities are reported for degraded-mode diagnostics.
    """

    if manifest.closure_hash != manifest.compute_hash():
        raise _error(
            manifest,
            ErrorCode.RUNTIME_CLOSURE_INVALID,
            "Runtime Closure manifest hash does not match its canonical payload",
        )
    if manifest.lifecycle != required_lifecycle:
        raise _error(
            manifest,
            ErrorCode.RUNTIME_CLOSURE_CANDIDATE_NOT_PUBLISHED,
            "Only production Runtime Closures may enter an install target",
            details={"lifecycle": manifest.lifecycle, "required": required_lifecycle},
        )

    resolved = dict(resolved_versions or {})
    verified: list[str] = []
    optional_missing: list[str] = []
    for resource in manifest.resources:
        if resolved and resource.resource_class != ResourceClass.OPTIONAL:
            resolved_version = resolved.get(resource.resource_id)
            if resolved_version is None:
                raise _error(
                    manifest,
                    ErrorCode.RUNTIME_CLOSURE_VERSION_MISMATCH,
                    f"No resolved version supplied for {resource.resource_id}",
                    details={"resource_id": resource.resource_id},
                )
            if resolved_version != resource.version:
                raise _error(
                    manifest,
                    ErrorCode.RUNTIME_CLOSURE_VERSION_MISMATCH,
                    f"Resolved version for {resource.resource_id} is incompatible",
                    details={
                        "resource_id": resource.resource_id,
                        "expected": resource.version,
                        "actual": resolved_version,
                    },
                )
        try:
            path = _safe_resource_path(root, resource)
        except ValueError as exc:
            raise _error(
                manifest,
                ErrorCode.RUNTIME_CLOSURE_INVALID,
                f"Resource path for {resource.resource_id} escapes the closure root",
                details={"resource_id": resource.resource_id},
            ) from exc
        if not path.is_file():
            if resource.resource_class == ResourceClass.OPTIONAL:
                optional_missing.append(resource.resource_id)
                continue
            raise _error(
                manifest,
                ErrorCode.RUNTIME_CLOSURE_RESOURCE_MISSING,
                f"Required Runtime Closure resource is missing: {resource.resource_id}",
                details={"resource_id": resource.resource_id},
            )
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != resource.sha256:
            raise _error(
                manifest,
                ErrorCode.RUNTIME_CLOSURE_HASH_MISMATCH,
                f"Runtime Closure resource hash mismatch: {resource.resource_id}",
                details={
                    "resource_id": resource.resource_id,
                    "expected": resource.sha256,
                    "actual": actual_hash,
                },
            )
        verified.append(resource.resource_id)

    available = dict(available_capabilities or {})
    resource_ids = _capability_resource_ids(manifest)
    optional_capabilities_missing: list[str] = []
    for capability in manifest.capabilities:
        if capability.kind == CapabilityKind.BUNDLED_SCRIPT:
            if capability.resource_id not in resource_ids:
                raise _error(
                    manifest,
                    ErrorCode.RUNTIME_CLOSURE_CAPABILITY_MISSING,
                    "Bundled capability resource is not pinned: "
                    f"{capability.capability_id}",
                    details={"capability_id": capability.capability_id},
                )
            continue
        if capability.kind == CapabilityKind.UNSUPPORTED:
            raise _error(
                manifest,
                ErrorCode.RUNTIME_CLOSURE_CAPABILITY_MISSING,
                f"Unsupported capability is declared: {capability.capability_id}",
                details={"capability_id": capability.capability_id},
            )
        actual_version = available.get(capability.capability_id)
        if actual_version is None:
            if capability.kind == CapabilityKind.HOST_OPTIONAL:
                optional_capabilities_missing.append(capability.capability_id)
                continue
            raise _error(
                manifest,
                ErrorCode.RUNTIME_CLOSURE_CAPABILITY_MISSING,
                f"Required host capability is unavailable: {capability.capability_id}",
                details={"capability_id": capability.capability_id},
            )
        if actual_version != capability.version:
            raise _error(
                manifest,
                ErrorCode.RUNTIME_CLOSURE_VERSION_MISMATCH,
                f"Capability version is incompatible: {capability.capability_id}",
                details={
                    "capability_id": capability.capability_id,
                    "expected": capability.version,
                    "actual": actual_version,
                },
            )

    if manifest.closure_hash is None:
        raise _error(
            manifest,
            ErrorCode.RUNTIME_CLOSURE_INVALID,
            "Runtime Closure manifest must carry a closure_hash before validation",
        )
    return ClosureValidationReport(
        closure_hash=manifest.closure_hash,
        verified_resource_ids=tuple(verified),
        optional_resources_missing=tuple(optional_missing),
        optional_capabilities_missing=tuple(optional_capabilities_missing),
    )


def write_manifest(
    root: Path,
    manifest: RuntimeClosureManifest,
    *,
    filename: str = "runtime-closure.json",
) -> Path:
    """Write a canonical, hash-bearing manifest under ``root``."""

    if manifest.closure_hash != manifest.compute_hash():
        raise _error(
            manifest,
            ErrorCode.RUNTIME_CLOSURE_INVALID,
            "Cannot write a Runtime Closure manifest with a stale hash",
        )
    destination = root / filename
    destination.write_text(
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


@dataclass(frozen=True)
class RuntimeClosureSession:
    """A validated session that never consults an active-pack pointer."""

    root: Path
    manifest: RuntimeClosureManifest
    report: ClosureValidationReport

    @classmethod
    def open(cls, root: Path) -> Self:
        manifest_path = root / "runtime-closure.json"
        try:
            manifest = RuntimeClosureManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise RuntimeClosureError(
                ErrorCode.RUNTIME_CLOSURE_INVALID,
                "runtime-closure",
                "Runtime Closure manifest cannot be loaded",
                details={"manifest": "runtime-closure.json"},
            ) from exc
        report = validate_closure(root, manifest)
        return cls(root=root.resolve(), manifest=manifest, report=report)

    @property
    def closure_hash(self) -> str:
        return self.report.closure_hash

    def assert_task_contract(
        self,
        task_contract_id: str,
        task_contract_version: str,
    ) -> None:
        """Reject a product descriptor bound to a different task contract."""

        if (
            self.manifest.task_contract_id == task_contract_id
            and self.manifest.task_contract_version == task_contract_version
        ):
            return
        raise RuntimeClosureError(
            ErrorCode.RUNTIME_CLOSURE_INVALID,
            self.manifest.task_contract_id,
            "Runtime Product task contract does not match its Closure",
            details={
                "closure_task_contract_id": self.manifest.task_contract_id,
                "closure_task_contract_version": self.manifest.task_contract_version,
                "product_task_contract_id": task_contract_id,
                "product_task_contract_version": task_contract_version,
            },
        )

    def assert_closure_hash(self, expected_closure_hash: str) -> None:
        """Reject a product descriptor bound to a different Closure payload."""

        if self.closure_hash == expected_closure_hash:
            return
        raise RuntimeClosureError(
            ErrorCode.RUNTIME_CLOSURE_INVALID,
            self.manifest.task_contract_id,
            "Runtime Product closure hash does not match its Closure",
            details={
                "closure_hash": self.closure_hash,
                "product_closure_hash": expected_closure_hash,
            },
        )

    def assert_asset_pack(
        self,
        pack_id: str,
        pack_version: str,
        pack_hash: str,
    ) -> None:
        """Reject a Closure bound to a different immutable Pack release."""

        if (
            self.manifest.asset_pack_id == pack_id
            and self.manifest.asset_pack_version == pack_version
            and self.manifest.asset_pack_hash == pack_hash
        ):
            return
        raise RuntimeClosureError(
            ErrorCode.RUNTIME_CLOSURE_INVALID,
            self.manifest.task_contract_id,
            "Runtime Closure asset Pack does not match the requested release",
            details={
                "closure_asset_pack_id": self.manifest.asset_pack_id,
                "closure_asset_pack_version": self.manifest.asset_pack_version,
                "closure_asset_pack_hash": self.manifest.asset_pack_hash,
                "requested_asset_pack_id": pack_id,
                "requested_asset_pack_version": pack_version,
                "requested_asset_pack_hash": pack_hash,
            },
        )

    def assert_capabilities(
        self,
        capabilities: Iterable[ToolCapability],
    ) -> None:
        """Reject a product descriptor with a different capability binding."""

        product_capabilities = tuple(capabilities)
        product_signature = _capability_signature(product_capabilities)
        closure_signature = _capability_signature(self.manifest.capabilities)
        if product_signature == closure_signature:
            return
        raise RuntimeClosureError(
            ErrorCode.RUNTIME_CLOSURE_INVALID,
            self.manifest.task_contract_id,
            "Runtime Product capabilities do not match its Closure",
            details={
                "closure_capability_ids": [
                    capability.capability_id
                    for capability in self.manifest.capabilities
                ],
                "product_capability_ids": [
                    capability.capability_id for capability in product_capabilities
                ],
            },
        )

    def resource_path(self, resource_id: str) -> Path:
        for resource in self.manifest.resources:
            if resource.resource_id == resource_id:
                return _safe_resource_path(self.root, resource)
        raise RuntimeClosureError(
            ErrorCode.RUNTIME_CLOSURE_RESOURCE_MISSING,
            self.manifest.task_contract_id,
            f"Resource is not pinned by this Runtime Closure: {resource_id}",
            details={"resource_id": resource_id},
        )

    def verify(self) -> ClosureValidationReport:
        """Recheck pinned files while ignoring any external active pointer."""

        return validate_closure(self.root, self.manifest)
