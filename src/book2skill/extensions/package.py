"""Extension package inspection and integrity verification.

An extension ships as a ZIP whose root contains ``extension-manifest.json`` and
a ``checksums.sha256`` file listing every payload file's digest. This module
parses a ZIP (or an extracted directory) into an :class:`InspectedExtension`
without installing anything, and verifies SHA-256 integrity — the mandatory
gate before any install/upgrade can proceed.
"""

from __future__ import annotations

import hashlib
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from book2skill.sdk.models import ExtensionManifest

MANIFEST_FILENAME = "extension-manifest.json"
CHECKSUMS_FILENAME = "checksums.sha256"

# Extension packages are executable inputs.  These ceilings prevent a malformed
# archive from exhausting memory before integrity verification can finish.
MAX_PACKAGE_FILES = 4_096
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024

_CHECKSUM_RECORD = re.compile(r"([0-9a-fA-F]{64})[ \t]+(\*?)(.+)")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}


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


def _normalize_member_path(raw: str, *, is_directory: bool = False) -> str:
    """Return a portable relative package path or reject ambiguous input."""
    if "\x00" in raw:
        raise ValueError("unsafe package path contains NUL")

    normal = raw.replace("\\", "/")
    if is_directory and normal.endswith("/"):
        normal = normal[:-1]
    windows_path = PureWindowsPath(normal)
    if (
        not normal
        or normal.startswith("/")
        or windows_path.drive
        or windows_path.root
    ):
        raise ValueError(f"unsafe package path: {raw!r}")

    parts = normal.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"unsafe package path: {raw!r}")
    if any(
        ":" in part
        or part.endswith((" ", "."))
        or any(ord(char) < 32 for char in part)
        or part.split(".", maxsplit=1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        for part in parts
    ):
        raise ValueError(f"unsafe package path: {raw!r}")
    return "/".join(parts)


def _path_key(rel: str) -> str:
    """Return a case-insensitive key matching the supported host platforms."""
    return unicodedata.normalize("NFC", rel).casefold()


def _require_contained(path: Path, root: Path, *, label: str) -> None:
    """Raise when resolved *path* is outside resolved *root*."""
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes package root") from exc


def _check_size_budget(*, rel: str, size: int, total: int) -> int:
    if size < 0 or size > MAX_FILE_BYTES:
        raise ValueError(
            f"package file exceeds {MAX_FILE_BYTES} byte limit: {rel}"
        )
    updated = total + size
    if updated > MAX_TOTAL_BYTES:
        raise ValueError(
            f"package exceeds {MAX_TOTAL_BYTES} byte uncompressed limit"
        )
    return updated


def _read_zip_entries(src: Path) -> dict[str, bytes]:
    """Read a ZIP after validating every member and its resource budget."""
    with zipfile.ZipFile(src, "r") as zf:
        infos = zf.infolist()
        if len(infos) > MAX_PACKAGE_FILES:
            raise ValueError(
                f"package contains too many files (limit {MAX_PACKAGE_FILES})"
            )

        normalized: list[tuple[zipfile.ZipInfo, str]] = []
        seen: dict[str, str] = {}
        total = 0
        for info in infos:
            rel = _normalize_member_path(
                info.filename, is_directory=info.is_dir()
            )
            file_type = stat.S_IFMT(info.external_attr >> 16)
            if file_type == stat.S_IFLNK:
                raise ValueError(
                    f"package member must not be a symbolic link: {rel}"
                )
            if info.is_dir() and info.file_size:
                raise ValueError(f"package directory contains data: {rel}")
            key = _path_key(rel)
            if key in seen:
                raise ValueError(
                    f"duplicate package member: {rel} conflicts with {seen[key]}"
                )
            seen[key] = rel
            normalized.append((info, rel))
            if not info.is_dir():
                total = _check_size_budget(
                    rel=rel, size=info.file_size, total=total
                )

        file_names = [rel for info, rel in normalized if not info.is_dir()]
        manifest_paths = [
            rel
            for rel in file_names
            if rel == MANIFEST_FILENAME
            or (
                rel.count("/") == 1
                and rel.endswith(f"/{MANIFEST_FILENAME}")
            )
        ]
        if not manifest_paths:
            return {}
        if len(manifest_paths) != 1:
            raise ValueError("multiple extension manifests in package")

        manifest_path = manifest_paths[0]
        root = manifest_path.rpartition("/")[0]
        root_prefix = f"{root}/" if root else ""
        entries: dict[str, bytes] = {}
        relative_seen: dict[str, str] = {}
        for info, archive_rel in normalized:
            if root:
                if archive_rel == root and info.is_dir():
                    continue
                if not archive_rel.startswith(root_prefix):
                    raise ValueError(
                        f"package member is outside package root: {archive_rel}"
                    )
                rel = archive_rel[len(root_prefix):]
            else:
                rel = archive_rel
            if info.is_dir():
                continue

            key = _path_key(rel)
            if key in relative_seen:
                raise ValueError(
                    f"duplicate package member: {rel} conflicts with "
                    f"{relative_seen[key]}"
                )
            relative_seen[key] = rel
            data = zf.read(info)
            if len(data) != info.file_size:
                raise ValueError(f"invalid uncompressed size for package member: {rel}")
            entries[rel] = data
        return entries


def _read_directory_entries(src: Path) -> dict[str, bytes]:
    """Read a directory package without following links outside its root."""
    root = src.resolve(strict=True)
    entries: dict[str, bytes] = {}
    seen: dict[str, str] = {}
    total = 0
    member_count = 0
    for path in src.rglob("*"):
        member_count += 1
        if member_count > MAX_PACKAGE_FILES:
            raise ValueError(
                f"package contains too many files (limit {MAX_PACKAGE_FILES})"
            )
        rel = _normalize_member_path(
            path.relative_to(src).as_posix(), is_directory=path.is_dir()
        )
        key = _path_key(rel)
        if key in seen:
            raise ValueError(
                f"duplicate package member: {rel} conflicts with {seen[key]}"
            )
        seen[key] = rel
        if path.is_symlink():
            raise ValueError(f"package member must not be a symbolic link: {rel}")

        resolved = path.resolve(strict=True)
        _require_contained(resolved, root, label=f"package member {rel}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"unsupported package member type: {rel}")
        size = path.stat().st_size
        total = _check_size_budget(rel=rel, size=size, total=total)
        data = path.read_bytes()
        if len(data) != size:
            raise ValueError(f"package member changed while reading: {rel}")
        _require_contained(
            path.resolve(strict=True), root, label=f"package member {rel}"
        )
        entries[rel] = data
    return entries


def _read_package_entries(src: Path) -> dict[str, bytes]:
    return _read_directory_entries(src) if src.is_dir() else _read_zip_entries(src)


def _verify_checksums(checksums_text: str, entries: dict[str, bytes]) -> None:
    """Verify digest syntax, unique records, inventory, and file contents."""
    expected: dict[str, str] = {}
    seen: dict[str, str] = {}
    for line_number, line in enumerate(checksums_text.splitlines(), start=1):
        if not line.strip():
            continue
        match = _CHECKSUM_RECORD.fullmatch(line)
        if match is None:
            raise ValueError(f"invalid checksum record at line {line_number}")
        digest, _binary_marker, raw_rel = match.groups()
        rel = _normalize_member_path(raw_rel)
        key = _path_key(rel)
        if key in seen:
            raise ValueError(
                f"duplicate checksum record for {rel} at line {line_number}"
            )
        seen[key] = rel
        expected[rel] = digest.lower()

    actual_files = set(entries) - {CHECKSUMS_FILENAME}
    expected_files = set(expected)
    if actual_files != expected_files:
        missing = sorted(actual_files - expected_files)
        unknown = sorted(expected_files - actual_files)
        details: list[str] = []
        if missing:
            details.append(f"missing records: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown records: {', '.join(unknown)}")
        raise ValueError(f"checksum inventory mismatch ({'; '.join(details)})")

    for rel in sorted(actual_files):
        actual = hashlib.sha256(entries[rel]).hexdigest()
        if actual != expected[rel]:
            raise ValueError(
                f"checksum mismatch for {rel}: "
                f"expected {expected[rel]}, got {actual}"
            )


def _inspect_entries(
    src: Path, entries: dict[str, bytes], *, verify_integrity: bool
) -> InspectedExtension:
    if MANIFEST_FILENAME not in entries:
        raise ValueError(f"missing {MANIFEST_FILENAME} in extension package")
    manifest = ExtensionManifest.model_validate_json(entries[MANIFEST_FILENAME])

    if verify_integrity:
        if CHECKSUMS_FILENAME not in entries:
            raise ValueError(
                f"missing {CHECKSUMS_FILENAME}; refusing un-verifiable install"
            )
        try:
            checksums_text = entries[CHECKSUMS_FILENAME].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{CHECKSUMS_FILENAME} must be UTF-8") from exc
        _verify_checksums(checksums_text, entries)

    payload = {
        k: v
        for k, v in entries.items()
        if k not in (MANIFEST_FILENAME, CHECKSUMS_FILENAME)
    }
    return InspectedExtension(
        manifest=manifest,
        source=src,
        file_count=len(payload),
        payload_bytes=sum(len(data) for data in payload.values()),
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
    entries = _read_package_entries(src)
    return _inspect_entries(
        src, entries, verify_integrity=verify_integrity
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
    normalized: dict[str, bytes] = {}
    seen: dict[str, str] = {}
    for raw_rel, data in files.items():
        rel = _normalize_member_path(raw_rel)
        key = _path_key(rel)
        if key in seen:
            raise ValueError(
                f"duplicate package member: {rel} conflicts with {seen[key]}"
            )
        seen[key] = rel
        normalized[rel] = data

    lines: list[str] = []
    for rel in sorted(normalized):
        if rel == CHECKSUMS_FILENAME:
            continue
        lines.append(f"{file_sha256(normalized[rel])}  {rel}")
    return "\n".join(lines) + ("\n" if lines else "")


def _safe_destination(root: Path, rel: str) -> Path:
    """Resolve one extraction destination and reject links or escapes."""
    normal = _normalize_member_path(rel)
    destination = root.joinpath(*normal.split("/"))
    _require_contained(
        destination.resolve(strict=False), root, label=f"target path {normal}"
    )

    current = root
    for part in normal.split("/")[:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"target path contains symbolic link: {normal}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"target parent is not a directory: {normal}")
    if destination.is_symlink():
        raise ValueError(f"target path is a symbolic link: {normal}")
    if destination.exists():
        raise ValueError(f"target path already exists: {normal}")
    return destination


def extract_payload(source: str | Path, target_dir: Path) -> InspectedExtension:
    """Copy a verified package's files into *target_dir* and return inspection.

    *target_dir* receives ``extension-manifest.json``, ``checksums.sha256`` and
    every payload file. Integrity is verified first via :func:`inspect_package`.
    """
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"extension package not found: {src}")
    entries = _read_package_entries(src)
    inspected = _inspect_entries(src, entries, verify_integrity=True)

    target = Path(target_dir)
    if target.is_symlink():
        raise ValueError("target staging directory must not be a symbolic link")
    target.mkdir(parents=True, exist_ok=True)
    target_root = target.resolve(strict=True)

    destinations = {
        rel: _safe_destination(target_root, rel) for rel in entries
    }
    for rel, data in entries.items():
        dest = destinations[rel]
        dest.parent.mkdir(parents=True, exist_ok=True)
        _require_contained(
            dest.parent.resolve(strict=True),
            target_root,
            label=f"target parent for {rel}",
        )
        _require_contained(
            dest.resolve(strict=False), target_root, label=f"target path {rel}"
        )
        if dest.is_symlink():
            raise ValueError(f"target path is a symbolic link: {rel}")
        dest.write_bytes(data)
        _require_contained(
            dest.resolve(strict=True), target_root, label=f"target path {rel}"
        )
    return inspected
