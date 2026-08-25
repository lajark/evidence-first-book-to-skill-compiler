"""Experimental emission of Runtime Product files from Build/Publish staging.

This module deliberately requires an explicit :class:`RuntimeClosureSpec` and
product descriptor. It never infers a task identity from a book name or a
collection id. Build emits a candidate Closure; Publisher emits production.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from book2skill.domain import SourceManifest
from book2skill.domain.errors import ErrorCode

from .closure import (
    CapabilityKind,
    ClosureLifecycle,
    ClosureResource,
    ProvenanceRef,
    ResourceClass,
    RuntimeClosureError,
    RuntimeClosureManifest,
)
from .product_manifest import RuntimeProductManifest, write_product_manifest
from .profiles import GeneratedSkillProduct


class RuntimeClosureSpec(BaseModel):
    """Explicit stable IDs and versions required to emit a Runtime Closure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    closure_version: str = Field(..., min_length=1)
    skill_kernel_id: str = Field(..., min_length=1)
    skill_kernel_version: str = Field(..., min_length=1)
    asset_pack_id: str = Field(..., min_length=1)
    asset_pack_version: str = Field(..., min_length=1)
    asset_pack_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    io_schema_id: str = Field(..., min_length=1)
    io_schema_version: str = Field(..., min_length=1)
    security_profile_id: str = Field(..., min_length=1)
    security_profile_version: str = Field(..., min_length=1)
    resource_version: str = Field(default="1.0.0", min_length=1)


@dataclass(frozen=True)
class RuntimeProductEmission:
    """The two descriptors emitted together in one staging tree."""

    product_manifest: RuntimeProductManifest
    closure_manifest: RuntimeClosureManifest


_ResourceKind = Literal["kernel", "asset_pack", "schema", "tool", "reference"]


def _resource_shape(relative: Path) -> tuple[_ResourceKind, ResourceClass] | None:
    """Classify only files that can be consumed by a generated Skill."""

    parts = relative.parts
    if relative.as_posix() == "SKILL.md":
        return "kernel", ResourceClass.EXACT_REQUIRED
    if not parts:
        return None
    if parts[0] == "schemas":
        return "schema", ResourceClass.EXACT_REQUIRED
    if parts[0] == "assets":
        return "asset_pack", ResourceClass.EXACT_REQUIRED
    if parts[0] in {"scripts", "tools"}:
        return "tool", ResourceClass.EXACT_REQUIRED
    if parts[0] == "references":
        return "reference", ResourceClass.SEMANTIC_RETRIEVAL
    return None


def _resource_provenance(
    relative: str,
    source_manifests: tuple[SourceManifest, ...],
    source_block_provenance: tuple[ProvenanceRef, ...] | None = None,
) -> list[ProvenanceRef]:
    if source_block_provenance is not None:
        return list(source_block_provenance)
    return [
        ProvenanceRef(
            source_id=manifest.source_id,
            locator=f"generated:{relative}",
            source_sha256=manifest.content_sha256,
        )
        for manifest in source_manifests
    ]


def _error(
    product: GeneratedSkillProduct,
    code: ErrorCode,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> RuntimeClosureError:
    return RuntimeClosureError(
        code,
        product.task_contract_id,
        message,
        details=details,
    )


class RuntimeProductEmitter:
    """Emit product and Closure descriptors into an already-built tree."""

    @classmethod
    def emit(
        cls,
        root: Path,
        product: GeneratedSkillProduct,
        closure_spec: RuntimeClosureSpec,
        *,
        lifecycle: ClosureLifecycle,
        source_manifests: Iterable[SourceManifest] = (),
        source_refs: Iterable[tuple[str, str]] | None = None,
    ) -> RuntimeProductEmission:
        root = root.resolve()
        if not root.is_dir():
            raise _error(
                product,
                ErrorCode.RUNTIME_CLOSURE_INVALID,
                "Runtime Product emission root must be an existing directory",
            )
        if not product.task_contract_id.strip():
            raise _error(
                product,
                ErrorCode.RUNTIME_CLOSURE_INVALID,
                "Product task_contract_id must not be blank",
            )

        manifests = tuple(source_manifests)
        if not manifests:
            raise _error(
                product,
                ErrorCode.RUNTIME_CLOSURE_INVALID,
                "Runtime Product emission requires source provenance manifests",
                details={"reason": "source_provenance_missing"},
            )
        source_ids = [manifest.source_id for manifest in manifests]
        if len(source_ids) != len(set(source_ids)):
            raise _error(
                product,
                ErrorCode.RUNTIME_CLOSURE_INVALID,
                "Runtime Product emission received duplicate source manifests",
                details={
                    "reason": "duplicate_source_manifest",
                    "source_ids": source_ids,
                },
            )
        block_provenance: tuple[ProvenanceRef, ...] | None = None
        if source_refs is not None:
            manifests_by_source = {
                manifest.source_id: manifest for manifest in manifests
            }
            refs: dict[tuple[str, str], ProvenanceRef] = {}
            for source_id, block_id in source_refs:
                if not source_id.strip() or not block_id.strip():
                    raise _error(
                        product,
                        ErrorCode.RUNTIME_CLOSURE_INVALID,
                        "Runtime Product source block locator must not be blank",
                        details={
                            "reason": "source_block_provenance_missing",
                            "source_id": source_id,
                            "block_id": block_id,
                        },
                    )
                manifest = manifests_by_source.get(source_id)
                if manifest is None:
                    raise _error(
                        product,
                        ErrorCode.RUNTIME_CLOSURE_INVALID,
                        "Runtime Product source block is not covered by a manifest",
                        details={
                            "reason": "source_block_provenance_missing",
                            "source_id": source_id,
                            "block_id": block_id,
                        },
                    )
                refs[(source_id, block_id)] = ProvenanceRef(
                    source_id=source_id,
                    locator=f"block:{block_id}",
                    source_sha256=manifest.content_sha256,
                )
            if not refs:
                raise _error(
                    product,
                    ErrorCode.RUNTIME_CLOSURE_INVALID,
                    "Runtime Product emission requires source block provenance",
                    details={"reason": "source_block_provenance_missing"},
                )
            block_provenance = tuple(
                refs[key] for key in sorted(refs, key=lambda item: (item[0], item[1]))
            )
        resources: list[ClosureResource] = []
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file() or path.is_symlink():
                continue
            relative_path = path.relative_to(root)
            shape = _resource_shape(relative_path)
            if shape is None:
                continue
            kind, resource_class = shape
            relative = relative_path.as_posix()
            resources.append(
                ClosureResource(
                    resource_id=f"resource:{relative}",
                    kind=kind,
                    resource_class=resource_class,
                    path=relative,
                    version=closure_spec.resource_version,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    provenance=_resource_provenance(
                        relative, manifests, block_provenance
                    ),
                )
            )

        if not resources:
            raise _error(
                product,
                ErrorCode.RUNTIME_CLOSURE_RESOURCE_MISSING,
                "Runtime Product emission found no pinned Skill resources",
            )
        resource_ids = {resource.resource_id for resource in resources}
        for capability in product.capabilities:
            if (
                capability.kind == CapabilityKind.BUNDLED_SCRIPT
                and capability.resource_id not in resource_ids
            ):
                raise _error(
                    product,
                    ErrorCode.RUNTIME_CLOSURE_CAPABILITY_MISSING,
                    "Bundled capability is not represented by an emitted resource",
                    details={"capability_id": capability.capability_id},
                )

        closure = RuntimeClosureManifest(
            closure_version=closure_spec.closure_version,
            lifecycle=lifecycle,
            task_contract_id=product.task_contract_id,
            task_contract_version=product.task_contract_version,
            skill_kernel_id=closure_spec.skill_kernel_id,
            skill_kernel_version=closure_spec.skill_kernel_version,
            asset_pack_id=closure_spec.asset_pack_id,
            asset_pack_version=closure_spec.asset_pack_version,
            asset_pack_hash=closure_spec.asset_pack_hash,
            io_schema_id=closure_spec.io_schema_id,
            io_schema_version=closure_spec.io_schema_version,
            security_profile_id=closure_spec.security_profile_id,
            security_profile_version=closure_spec.security_profile_version,
            resources=resources,
            capabilities=product.capabilities,
            closure_hash=None,
        ).with_hash()
        product_manifest = RuntimeProductManifest(
            product=product,
            closure_hash=closure.closure_hash,
        ).with_hash()
        write_product_manifest(root, product, closure_hash=closure.closure_hash)
        # Write the Closure last: a caller can treat its presence as the final
        # marker that the product descriptor pair was emitted in staging.
        from .closure import write_manifest

        write_manifest(root, closure)
        return RuntimeProductEmission(
            product_manifest=product_manifest,
            closure_manifest=closure,
        )


__all__ = [
    "RuntimeClosureSpec",
    "RuntimeProductEmission",
    "RuntimeProductEmitter",
]
