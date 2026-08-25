"""Bind an immutable Asset Pack release to a production Runtime Closure."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from book2skill.domain.errors import ErrorCode
from book2skill.storage import atomic_write

from .closure import (
    ClosureResource,
    ProvenanceRef,
    ResourceClass,
    RuntimeClosureError,
    RuntimeClosureManifest,
    RuntimeClosureSession,
    write_manifest,
)
from .pack_update import AssetPackRelease
from .product_manifest import (
    ProductManifestError,
    RuntimeProductManifest,
    write_product_manifest,
)

_PACK_RESOURCE_PATH = "assets/pack-release.json"


@dataclass(frozen=True)
class PackClosureBinding:
    """Evidence returned after binding one Pack release to a Closure."""

    pack_id: str
    pack_version: str
    pack_hash: str
    closure_hash: str
    resource_id: str


def _error(manifest: RuntimeClosureManifest, message: str) -> RuntimeClosureError:
    return RuntimeClosureError(
        ErrorCode.RUNTIME_CLOSURE_INVALID,
        manifest.task_contract_id,
        message,
        details={"asset_pack_id": manifest.asset_pack_id},
    )


def _release_payload(release: AssetPackRelease) -> bytes:
    return (
        json.dumps(
            release.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _unique_provenance(release: AssetPackRelease) -> list[ProvenanceRef]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[ProvenanceRef] = []
    for asset in release.assets:
        for ref in asset.provenance:
            key = (ref.source_id, ref.locator, ref.source_sha256)
            if key not in seen:
                seen.add(key)
                result.append(ref)
    return result


class PackClosureBinder:
    """Materialize and pin a Pack release inside a production Skill Closure."""

    @classmethod
    def bind(cls, root: Path, release: AssetPackRelease) -> PackClosureBinding:
        if release.pack_hash != release.compute_hash():
            raise RuntimeClosureError(
                ErrorCode.PACK_UPDATE_INVALID,
                release.task_contract_id,
                "Cannot bind a Pack release with a stale hash",
                details={"pack_id": release.pack_id, "version": release.version},
            )
        provenance = _unique_provenance(release)
        if not provenance:
            raise RuntimeClosureError(
                ErrorCode.RUNTIME_CLOSURE_INVALID,
                release.task_contract_id,
                "Pack Closure binding requires source provenance",
                details={"pack_id": release.pack_id, "version": release.version},
            )

        root = root.resolve()
        try:
            session = RuntimeClosureSession.open(root)
            product_manifest = RuntimeProductManifest.from_file(
                root / "runtime-product.json"
            )
        except (RuntimeClosureError, ProductManifestError) as exc:
            raise RuntimeClosureError(
                ErrorCode.RUNTIME_CLOSURE_INVALID,
                release.task_contract_id,
                "Production Runtime Product/Closure cannot be opened for Pack binding",
                details={"reason": "closure_open_failed"},
            ) from exc

        manifest = session.manifest
        if (
            manifest.task_contract_id != release.task_contract_id
            or manifest.task_contract_version != release.task_contract_version
            or manifest.skill_kernel_id != release.skill_kernel_id
            or manifest.skill_kernel_version != release.skill_kernel_version
        ):
            raise _error(
                manifest, "Pack release is incompatible with the Closure contract"
            )
        if product_manifest.product.task_contract_id != release.task_contract_id:
            raise _error(
                manifest, "Pack release is incompatible with the Product contract"
            )
        if manifest.asset_pack_id != release.pack_id:
            raise _error(manifest, "Pack release identity does not match the Closure")

        payload = _release_payload(release)
        pack_path = root / Path(*_PACK_RESOURCE_PATH.split("/"))
        pack_path.parent.mkdir(parents=True, exist_ok=True)
        if pack_path.exists() and pack_path.read_bytes() != payload:
            try:
                previous = AssetPackRelease.model_validate(
                    json.loads(pack_path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError) as exc:
                raise _error(
                    manifest, "Pinned Pack resource content is not a valid release"
                ) from exc
            if (
                previous.pack_id == release.pack_id
                and previous.version == release.version
                and previous.pack_hash != release.pack_hash
            ):
                raise _error(
                    manifest,
                    "Pinned Pack resource conflicts with the immutable release hash",
                )
        atomic_write(pack_path, payload)
        resource_id = f"pack:{release.pack_id}:{release.version}"
        pack_resource = ClosureResource(
            resource_id=resource_id,
            kind="asset_pack",
            resource_class=ResourceClass.EXACT_REQUIRED,
            path=_PACK_RESOURCE_PATH,
            version=release.version,
            sha256=hashlib.sha256(payload).hexdigest(),
            provenance=provenance,
        )
        resources = [
            resource
            for resource in manifest.resources
            if resource.path != _PACK_RESOURCE_PATH
        ]
        resources.append(pack_resource)
        rebound = manifest.model_copy(
            update={
                "asset_pack_version": release.version,
                "asset_pack_hash": release.pack_hash,
                "resources": sorted(resources, key=lambda item: item.path),
            }
        ).with_hash()
        write_manifest(root, rebound)
        write_product_manifest(
            root,
            product_manifest.product,
            closure_hash=rebound.closure_hash,
        )
        return PackClosureBinding(
            pack_id=release.pack_id,
            pack_version=release.version,
            pack_hash=release.pack_hash or "",
            closure_hash=rebound.closure_hash or "",
            resource_id=resource_id,
        )


__all__ = ["PackClosureBinder", "PackClosureBinding"]
