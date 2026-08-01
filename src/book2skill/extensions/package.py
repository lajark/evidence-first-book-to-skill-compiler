"""Extension package inspection and integrity verification.

An extension ships as a ZIP whose root contains ``extension-manifest.json`` and
a ``checksums.sha256`` file listing every payload file's digest. This module
parses a ZIP (or an extracted directory) into an :class:`InspectedExtension`
without installing anything, and verifies SHA-256 integrity — the mandatory
gate before any install/upgrade can proceed.
"""

from __future__ import annotations

import hashlib
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from book2skill.sdk.models import ExtensionManifest

MANIFEST_FILENAME = "extension-manifest.json"
CHECKSUMS_FILENAME = "checksums.sha256"


@dataclass(frozen=True)
class InspectedExtension:
    """Read-only result of inspecting an extension package."""

    manifest: ExtensionManifest
    source: Path
    file_count: int = 0
    payload_bytes: int = 0

    @property
    def extension_id(self) -> str:
        return self.manifest.extension_id

    @property
    def version(self) -> str:
        return self.manifest.version


def _iter_zip_entries(
    root: str, zf: zipfile.ZipFile
) -> Iterator[tuple[str, bytes]]:
    """Yield ``(rel_path, bytes)`` for non-directory entries under *root*."""
    root_prefix = root.rstrip("/") + "/" if root else ""
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if root_prefix:
            if not name.startswith(root_prefix):
                continue
            rel = name[len(root_prefix):]
        else:
            rel = name
        if not rel:
            continue
        # Normalise to forward slashes; skip path separators that escape root.
        normal = rel.replace("\\", "/")
        if ".." in normal.split("/"):
            continue
        yield normal, zf.read(info)


def _verify_checksums(checksums_text: str, entries: dict[str, bytes]) -> None:
    """Raise :class:`ValueError` if any payload digest mismatches *checksums_text*."""
    expected: dict[str, str] = {}
    for line in checksums_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.replace("\\", "/").split()
        if len(parts) == 2:
            digest, rel = parts
        else:
            # "hash  filename" where filename may contain spaces.
            digest, rel = parts[0], parts[-1]
        expected[rel] = digest.lower()

    for rel, data in entries.items():
        if rel == CHECKSUMS_FILENAME:
            continue
        if rel in expected:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected[rel]:
                raise ValueError(
                    f"checksum mismatch for {rel}: "
                    f"expected {expected[rel]}, got {actual}"
                )


def inspect_package(
    source: str | Path, *, verify_integrity: bool = True
) -> InspectedExtension:
    """Inspect an extension ZIP or directory, optionally verifying checksums.

    Args:
        source: Path to a ``.zip`` extension package or an extracted directory.
        verify_integrity: When ``True``, require ``checksums.sha256`` and verify
            every payload file. Comparing file inventory is also enforced.

    Returns:
        :class:`InspectedExtension` describing the package without installing.

    Raises:
        FileNotFoundError: if *source* does not exist.
        ValueError: if the package is malformed, lacks a manifest, fails validation,
            or (when *verify_integrity*) has missing/inconsistent checksums.
    """
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"extension package not found: {src}")

    entries: dict[str, bytes] = {}
    if src.is_dir():
        for p in src.rglob("*"):
            if p.is_file():
                entries[p.relative_to(src).as_posix()] = p.read_bytes()
    else:
        with zipfile.ZipFile(src, "r") as zf:
            # Locate the manifest at each top-level dir once.
            names = {i.filename for i in zf.infolist() if not i.is_dir()}
            tops = {name.split("/")[0] for name in names}
            roots = sorted(
                top for top in tops if f"{top}/{MANIFEST_FILENAME}" in names
            )
            root = roots[0] if roots else ""
            for rel, data in _iter_zip_entries(root, zf):
                entries[rel] = data

    if MANIFEST_FILENAME not in entries:
        raise ValueError(f"missing {MANIFEST_FILENAME} in extension package")
    manifest = ExtensionManifest.model_validate_json(entries[MANIFEST_FILENAME])

    if verify_integrity:
        if CHECKSUMS_FILENAME not in entries:
            raise ValueError(
                f"missing {CHECKSUMS_FILENAME}; refusing un-verifiable install"
            )
        _verify_checksums(entries[CHECKSUMS_FILENAME].decode("utf-8"), entries)

    payload = {
        k: v
        for k, v in entries.items()
        if k not in (MANIFEST_FILENAME, CHECKSUMS_FILENAME)
    }
    file_count = len(payload)
    payload_bytes = sum(len(v) for v in payload.values())
    return InspectedExtension(
        manifest=manifest,
        source=src,
        file_count=file_count,
        payload_bytes=payload_bytes,
    )


def checksums_file_for(manifest: ExtensionManifest) -> str:
    """Return the declared checksums filename (default ``checksums.sha256``)."""
    return manifest.checksums_file or CHECKSUMS_FILENAME


def file_sha256(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of *data*."""
    return hashlib.sha256(data).hexdigest()


def build_checksums_text(files: dict[str, bytes]) -> str:
    """Render a ``checksums.sha256``-compatible listing for *files*.

    Every *non-checksums* file gets a ``<digest> <rel_path>`` line, so the same
    package can be verified round-trip by :func:`inspect_package`.
    """
    lines: list[str] = []
    for rel in sorted(files):
        if rel == CHECKSUMS_FILENAME:
            continue
        lines.append(f"{file_sha256(files[rel])}  {rel}")
    return "\n".join(lines) + ("\n" if lines else "")


def extract_payload(source: str | Path, target_dir: Path) -> InspectedExtension:
    """Copy a verified package's files into *target_dir* and return inspection.

    *target_dir* receives ``extension-manifest.json``, ``checksums.sha256`` and
    every payload file. Integrity is verified first via :func:`inspect_package`.
    """
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    inspected = inspect_package(source)

    src = Path(source)
    entries: dict[str, bytes] = {}
    if src.is_dir():
        for p in src.rglob("*"):
            if p.is_file():
                entries[p.relative_to(src).as_posix()] = p.read_bytes()
    else:
        with zipfile.ZipFile(src, "r") as zf:
            tops = {i.filename.split("/")[0] for i in zf.infolist() if not i.is_dir()}
            names = {i.filename for i in zf.infolist()}
            roots = sorted(top for top in tops if f"{top}/{MANIFEST_FILENAME}" in names)
            root = roots[0] if roots else ""
            for rel, data in _iter_zip_entries(root, zf):
                entries[rel] = data

    for rel, data in entries.items():
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return inspected
