"""Versioned product descriptors for explicit Runtime Product installs.

The descriptor is intentionally separate from the Runtime Closure manifest:
the Closure freezes files and capabilities, while this document identifies the
task product and its Standalone/Extension-backed distribution profile.  A
caller must opt in to loading it; legacy Skill directories remain readable by
the existing installer API.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.storage.file_storage import atomic_write

from .profiles import GeneratedSkillProduct, HostRuntime

_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:")
PRODUCT_MANIFEST_FILENAME = "runtime-product.json"


class ProductManifestError(DomainError):
    """Stable, content-safe error raised while loading a product descriptor."""

    def __init__(
        self,
        message: str,
        *,
        filename: str = PRODUCT_MANIFEST_FILENAME,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            ErrorCode.PROFILE_MANIFEST_INVALID,
            filename,
            message,
            recovery=(
                "Regenerate a versioned runtime-product.json and verify its "
                "manifest hash before retrying."
            ),
            details=details,
        )


class RuntimeProductManifest(BaseModel):
    """Hash-addressed product descriptor with an optional Closure pin.

    Legacy descriptor files may omit ``closure_hash`` for read-only tooling,
    but the opt-in installer requires the pin before it mutates a host target.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    product: GeneratedSkillProduct
    closure_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    manifest_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    def canonical_payload(self) -> dict[str, object]:
        """Return the deterministic hash input without the self-hash field."""

        return self.model_dump(mode="json", exclude={"manifest_hash"})

    def compute_hash(self) -> str:
        payload = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def with_hash(self) -> Self:
        return self.model_copy(update={"manifest_hash": self.compute_hash()})

    @classmethod
    def from_file(cls, path: Path) -> Self:
        """Load and verify a descriptor without echoing malformed content."""

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            manifest = cls.model_validate(payload)
        except (OSError, ValueError, ValidationError) as exc:
            raise ProductManifestError(
                "Runtime product manifest cannot be loaded or validated",
                filename=path.name,
            ) from exc
        if manifest.manifest_hash != manifest.compute_hash():
            raise ProductManifestError(
                "Runtime product manifest hash does not match its canonical payload",
                filename=path.name,
            )
        return manifest


def _safe_filename(filename: str) -> str:
    """Allow only one relative POSIX filename below a product root."""

    if (
        not filename
        or "\\" in filename
        or filename.startswith("/")
        or _WINDOWS_ABSOLUTE.match(filename)
    ):
        raise ProductManifestError("Runtime product manifest filename is invalid")
    parsed = PurePosixPath(filename)
    if len(parsed.parts) != 1 or parsed.parts[0] in {"", ".", ".."}:
        raise ProductManifestError("Runtime product manifest filename is invalid")
    return filename


def write_product_manifest(
    root: Path,
    product: GeneratedSkillProduct,
    *,
    closure_hash: str | None = None,
    filename: str = PRODUCT_MANIFEST_FILENAME,
) -> Path:
    """Atomically write a hash-bearing product descriptor below *root*."""

    safe_name = _safe_filename(filename)
    destination = root.resolve() / safe_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = RuntimeProductManifest(
        product=product,
        closure_hash=closure_hash,
    ).with_hash()
    atomic_write(
        destination,
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return destination


def load_product_manifest(
    path_or_root: Path,
    *,
    filename: str = PRODUCT_MANIFEST_FILENAME,
) -> RuntimeProductManifest:
    """Load a descriptor from a file or from a Skill root directory."""

    safe_name = _safe_filename(filename)
    path = (
        path_or_root
        if path_or_root.suffix.lower() == ".json"
        else path_or_root / safe_name
    )
    return RuntimeProductManifest.from_file(path)


def load_host_runtime(path: Path) -> HostRuntime:
    """Load an explicit host capability snapshot for Extension-backed installs."""

    try:
        return HostRuntime.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        raise ProductManifestError(
            "Host runtime snapshot cannot be loaded or validated",
            filename=path.name,
        ) from exc


__all__ = [
    "PRODUCT_MANIFEST_FILENAME",
    "ProductManifestError",
    "RuntimeProductManifest",
    "load_host_runtime",
    "load_product_manifest",
    "write_product_manifest",
]
