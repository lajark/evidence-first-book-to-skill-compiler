"""Input discovery and legality gate.

Validates that every input file is legal to process — exists, non-empty, within
size limits, has a recognised format, and is not a zip-bomb — before producing
stable ``DiscoveredFile`` records keyed by content hash.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from book2skill.domain import ErrorCode, SourceFormat

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB
"""Maximum allowed source file size in bytes."""

_MAX_ZIP_RATIO = 100
"""Maximum allowed compression ratio for ZIP-based formats (EPUB, DOCX, …).

A ratio exceeding this threshold is treated as a potential zip-bomb.
"""

#: Minimum content length for content-based magic-byte detection.
_MIN_MAGIC_READ = 8

# ---------------------------------------------------------------------------
# Extension → SourceFormat mapping
# ---------------------------------------------------------------------------

_EXTENSION_MAP: dict[str, SourceFormat] = {
    ".pdf": SourceFormat.PDF,
    ".epub": SourceFormat.EPUB,
    ".mobi": SourceFormat.MOBI,
    ".azw": SourceFormat.AZW,
    ".azw3": SourceFormat.AZW3,
    ".txt": SourceFormat.TXT,
    ".text": SourceFormat.TXT,
    ".md": SourceFormat.MD,
    ".markdown": SourceFormat.MD,
    ".docx": SourceFormat.DOCX,
    ".html": SourceFormat.HTML,
    ".htm": SourceFormat.HTML,
    ".rtf": SourceFormat.RTF,
}

# ---------------------------------------------------------------------------
# Magic-byte signatures for content-based detection
# ---------------------------------------------------------------------------

# Each entry: (format, bytes_signature, offset)
_MAGIC_SIGNATURES: list[tuple[SourceFormat, bytes, int]] = [
    (SourceFormat.PDF, b"%PDF-", 0),
    (SourceFormat.EPUB, b"PK\x03\x04", 0),
    (SourceFormat.DOCX, b"PK\x03\x04", 0),
    (SourceFormat.MOBI, b"BOOKMOBI", 0),
]

# Recognised text-like formats (no magic bytes, checked via content sampling).
_TEXT_FORMATS: set[SourceFormat] = {
    SourceFormat.TXT,
    SourceFormat.MD,
    SourceFormat.HTML,
    SourceFormat.RTF,
}

# ZIP-based formats that need extra decompression-bomb checks.
_ZIP_FORMATS: set[SourceFormat] = {
    SourceFormat.EPUB,
    SourceFormat.DOCX,
}

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    """A file that passed the gate and is ready for extraction."""

    path: Path
    """Absolute path to the source file on disk."""

    original_name: str
    """Original filename (last component of *path*)."""

    content_sha256: str
    """Lowercase hex SHA-256 of the file content."""

    source_id: str
    """Stable content-derived identifier (first 12 hex chars of the hash)."""

    format: SourceFormat
    """Detected source format."""

    file_size: int
    """File size in bytes."""

    format_source: Literal["extension", "content", "both"]
    """How the format was determined."""


@dataclass(frozen=True, slots=True)
class GateError:
    """A file that failed gate validation."""

    path: Path
    """Absolute path of the file that failed."""

    code: ErrorCode
    """Stable machine-readable error code."""

    message: str
    """Human-readable error message."""

    recovery: str = ""
    """Suggested recovery action."""


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class Gate:
    """Discover, validate and deduplicate input files.

    Usage::

        gate = Gate()
        files, errors = gate.discover(["docs/", "extra.pdf"])
        for f in files:
            print(f.source_id, f.format)
    """

    # ---- public API -------------------------------------------------------

    def discover(
        self,
        inputs: list[str],
        *,
        glob: bool = True,
        recursive: bool = True,
    ) -> tuple[list[DiscoveredFile], list[GateError]]:
        """Discover and validate input files.

        Args:
            inputs: Paths, directory paths, or glob patterns. Relative paths
                are resolved against the current working directory.
            glob: If ``True`` (default), expand glob wildcards in each input.
            recursive: If ``True`` (default), recurse into subdirectories when
                an input is a directory.

        Returns:
            A ``(files, errors)`` tuple. *files* are deduplicated by content
            hash; *errors* contains one entry per file that could not be
            validated.
        """
        paths = self._expand_paths(inputs, glob=glob, recursive=recursive)
        if not paths:
            return [], []

        discovered: list[DiscoveredFile] = []
        errors: list[GateError] = []

        for path in paths:
            err = self._validate_basic(path)
            if err:
                errors.append(err)
                continue

            fmt, fmt_source = self._detect_format(path)
            if fmt is None:
                errors.append(
                    GateError(
                        path=path.resolve(),
                        code=ErrorCode.GATE_UNSUPPORTED_FORMAT,
                        message=(
                            f"Unsupported format: "
                            f"{path.suffix or '(no extension)'}"
                        ),
                        recovery=(
                            "Convert the file to a supported format "
                            "(PDF, EPUB, TXT, …)."
                        ),
                    )
                )
                continue

            # ZIP-bomb check for archive-based formats.
            if fmt in _ZIP_FORMATS:
                zip_err = self._check_zip_bomb(path)
                if zip_err:
                    errors.append(zip_err)
                    continue

            sha256 = self._compute_sha256(path)
            source_id = _source_id_from_hash(sha256)

            discovered.append(
                DiscoveredFile(
                    path=path.resolve(),
                    original_name=path.name,
                    content_sha256=sha256,
                    source_id=source_id,
                    format=fmt,
                    file_size=path.stat().st_size,
                    format_source=fmt_source,
                )
            )

        return self._deduplicate(discovered), errors

    # ---- path expansion ---------------------------------------------------

    @staticmethod
    def _expand_paths(
        inputs: list[str],
        *,
        glob: bool,
        recursive: bool,
    ) -> list[Path]:
        """Expand a list of user inputs to concrete file paths.

        Non-existent paths are included so the caller can report them as
        errors.
        """
        result: list[Path] = []
        seen: set[Path] = set()

        for raw in inputs:
            # Resolve the raw input to an absolute path for existence checks.
            raw_path = Path(raw)
            if not raw_path.is_absolute():
                raw_path = Path.cwd() / raw_path

            if glob and _has_glob_chars(raw):
                # Split into base directory and relative pattern.
                # e.g. "/tmp/*.txt" → base="/tmp", pattern="*.txt"
                # e.g. "/tmp/**/*.txt" → base="/tmp", pattern="**/*.txt"
                parts = raw_path.parts
                glob_idx = next(
                    (i for i, p in enumerate(parts) if _has_glob_chars(p)),
                    len(parts),
                )
                base = Path(*parts[:glob_idx])
                pattern = str(Path(*parts[glob_idx:]))

                # Do not let a glob traverse a symlinked directory.  Keep the
                # link as a candidate so ``_validate_basic`` returns a stable
                # error instead of silently dropping user input.
                symlink = _find_symlink_component(base)
                if symlink is not None:
                    candidate = base.absolute()
                    if candidate not in seen:
                        seen.add(candidate)
                        result.append(candidate)
                    continue

                if base.exists() and base.is_dir():
                    for match in sorted(base.glob(pattern)):
                        abs_match = match.absolute()
                        if _find_symlink_component(abs_match) is not None:
                            candidate = abs_match
                        elif abs_match.is_file():
                            candidate = abs_match.resolve()
                        else:
                            continue
                        if candidate not in seen:
                            seen.add(candidate)
                            result.append(candidate)
                continue

            # Resolve only trusted paths.  Resolving first would erase the
            # fact that the user supplied a symlink and could escape the
            # intended input directory.
            if _find_symlink_component(raw_path) is not None:
                candidate = raw_path.absolute()
                if candidate not in seen:
                    seen.add(candidate)
                    result.append(candidate)
                continue

            resolved = raw_path.absolute().resolve()
            if resolved.is_dir():
                for fpath in _walk_files(resolved, recursive=recursive):
                    if fpath not in seen:
                        seen.add(fpath)
                        result.append(fpath)
            elif (resolved.is_file() or not resolved.exists()) and resolved not in seen:
                seen.add(resolved)
                result.append(resolved)

        return result

    # ---- validation -------------------------------------------------------

    @staticmethod
    def _validate_basic(path: Path) -> GateError | None:
        """Check existence, readability, size and emptiness."""
        absolute = path.absolute()
        symlink = _find_symlink_component(absolute)
        if symlink is not None:
            return GateError(
                path=absolute,
                code=ErrorCode.GATE_SYMLINK_NOT_ALLOWED,
                message=f"Symbolic links are not allowed: {symlink}",
                recovery=(
                    "Provide a regular file or directory path instead of a "
                    "symbolic link."
                ),
            )

        resolved = absolute.resolve()

        if not resolved.exists():
            return GateError(
                path=resolved,
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                message=f"File not found: {path}",
                recovery="Check the path and try again.",
            )

        if not resolved.is_file():
            return GateError(
                path=resolved,
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                message=f"Not a regular file: {path}",
                recovery=(
                    "Book2Skill only processes regular files, "
                    "not directories or special files."
                ),
            )

        try:
            stat = resolved.stat()
        except OSError as exc:
            return GateError(
                path=resolved,
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                message=f"Cannot stat file: {exc}",
                recovery="Check file permissions.",
            )

        if stat.st_size == 0:
            return GateError(
                path=resolved,
                code=ErrorCode.GATE_EMPTY_FILE,
                message=f"File is empty: {path}",
                recovery="Provide a non-empty source file.",
            )

        if stat.st_size > MAX_FILE_SIZE:
            return GateError(
                path=resolved,
                code=ErrorCode.GATE_TOO_LARGE,
                message=f"File too large: {_format_size(stat.st_size)} "
                f"(max {_format_size(MAX_FILE_SIZE)})",
                recovery="Reduce the file size or split into smaller documents.",
            )

        if not os.access(resolved, os.R_OK):
            return GateError(
                path=resolved,
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                message=f"File is not readable: {path}",
                recovery="Check file permissions.",
            )

        return None

    # ---- format detection -------------------------------------------------

    @staticmethod
    def _detect_format(
        path: Path,
    ) -> tuple[SourceFormat | None, Literal["extension", "content", "both"]]:
        """Detect the source format using extension and content probing.

        Returns:
            ``(format, source)`` — *format* is ``None`` when neither method
            succeeds; *source* indicates the detection method used.
        """
        ext_fmt = _EXTENSION_MAP.get(path.suffix.lower())
        content_fmt = _detect_by_content(path)

        if ext_fmt and content_fmt:
            if ext_fmt == content_fmt:
                return ext_fmt, "both"
            # Extension and content disagree — trust content, but note the
            # mismatch.  The caller can log a warning.
            return content_fmt, "content"
        if content_fmt:
            return content_fmt, "content"
        if ext_fmt:
            return ext_fmt, "extension"
        return None, "extension"

    # ---- ZIP bomb check ---------------------------------------------------

    @staticmethod
    def _check_zip_bomb(path: Path) -> GateError | None:
        """Check whether a ZIP-based file is a decompression bomb."""
        try:
            with zipfile.ZipFile(path, "r") as zf:
                total_compressed = 0
                total_uncompressed = 0
                for info in zf.infolist():
                    total_compressed += info.compress_size
                    total_uncompressed += info.file_size
                    if (
                    total_compressed > 0
                    and total_uncompressed / total_compressed
                    > _MAX_ZIP_RATIO
                ):
                        return GateError(
                            path=path.resolve(),
                            code=ErrorCode.GATE_DAMAGED_FILE,
                            message=f"Possible zip-bomb detected: compression ratio "
                            f"{total_uncompressed / total_compressed:.0f}:1 exceeds "
                            f"limit of {_MAX_ZIP_RATIO}:1",
                            recovery="The file may be malicious. Verify its origin.",
                        )
        except (zipfile.BadZipFile, OSError) as exc:
            return GateError(
                path=path.resolve(),
                code=ErrorCode.GATE_DAMAGED_FILE,
                message=f"Invalid or corrupted ZIP file: {exc}",
                recovery="Re-download or re-create the file.",
            )
        return None

    # ---- hashing ----------------------------------------------------------

    @staticmethod
    def _compute_sha256(path: Path) -> str:
        """Compute the SHA-256 hex digest of a file."""
        sha = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)  # 1 MiB chunks
                if not chunk:
                    break
                sha.update(chunk)
        return sha.hexdigest()

    # ---- deduplication ----------------------------------------------------

    @staticmethod
    def _deduplicate(files: list[DiscoveredFile]) -> list[DiscoveredFile]:
        """Deduplicate files by content hash, keeping the first occurrence."""
        seen: dict[str, DiscoveredFile] = {}
        for f in files:
            if f.content_sha256 not in seen:
                seen[f.content_sha256] = f
        return list(seen.values())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_id_from_hash(sha256: str) -> str:
    """Derive a stable source id from the first 12 hex chars of the hash."""
    return sha256[:12]


def _has_glob_chars(s: str) -> bool:
    """Return ``True`` if *s* contains glob wildcard characters."""
    return any(ch in s for ch in ("*", "?", "["))


def _walk_files(root: Path, *, recursive: bool) -> list[Path]:
    """List files under *root* without following symbolic links.

    Symlink entries are returned as candidates so the gate can report an
    explicit ``GATE_SYMLINK_NOT_ALLOWED`` error.  Real files are canonicalised
    only after the containment check; this prevents recursive discovery from
    escaping its root through a link or a concurrent path substitution.
    """
    root = root.absolute().resolve()
    paths: list[Path] = []
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            candidate = entry.absolute()
            if _find_symlink_component(candidate) is not None:
                paths.append(candidate)
                continue
            try:
                canonical = candidate.resolve()
                canonical.relative_to(root)
            except (OSError, ValueError):
                # A non-link escape should not be possible, but fail closed if
                # the filesystem changes while discovery is in progress.
                paths.append(candidate)
                continue
            if entry.is_file():
                paths.append(canonical)
            elif recursive and entry.is_dir():
                pending.append(canonical)
    return sorted(paths)


def _find_symlink_component(path: Path) -> Path | None:
    """Return the first symlink in *path* or one of its parent components.

    ``Path.resolve`` follows links, so this check must run on an absolute but
    unresolved path.  Checking parents also catches ``link-dir/file.txt``
    where the leaf itself is not a symlink.
    """
    try:
        absolute = Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError):
        absolute = path
    for candidate in (absolute, *absolute.parents):
        try:
            if candidate.is_symlink():
                return candidate
        except OSError:
            # Let the normal readability/stat checks produce the actionable
            # error for inaccessible paths.
            continue
    return None


def _detect_by_content(path: Path) -> SourceFormat | None:
    """Detect source format by reading magic bytes at the start of the file.

    For ZIP-based formats (EPUB, DOCX) we peek inside the archive to
    disambiguate.
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(_MIN_MAGIC_READ)
    except OSError:
        return None

    if not header:
        return None

    for fmt, sig, offset in _MAGIC_SIGNATURES:
        if header[offset : offset + len(sig)] == sig:
            if fmt == SourceFormat.EPUB or fmt == SourceFormat.DOCX:
                # Disambiguate ZIP-based formats.
                return _disambiguate_zip(path)
            return fmt

    # Check for text-like formats via content sampling.
    if _looks_like_text(header):
        # Try to narrow down.
        text = _read_text_head(path)
        if text is not None:
            return _guess_text_format(text, path)

    return None


def _disambiguate_zip(path: Path) -> SourceFormat | None:
    """Peek inside a ZIP file to determine EPUB vs DOCX vs generic ZIP."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = {info.filename for info in zf.infolist()}
    except (zipfile.BadZipFile, OSError):
        return None

    if "mimetype" in names:
        try:
            with zipfile.ZipFile(path, "r") as zf:
                mime = zf.read("mimetype").decode("utf-8", errors="replace").strip()
                if "epub" in mime.lower():
                    return SourceFormat.EPUB
        except Exception:
            pass
    if "META-INF/encryption.xml" in names:
        return SourceFormat.EPUB
    if "[Content_Types].xml" in names and any(
        n.startswith("word/") for n in names
    ):
        return SourceFormat.DOCX

    return None


def _looks_like_text(header: bytes) -> bool:
    """Heuristic: check if the first bytes look like a text file.

    Returns ``True`` when the header is consistent with a text encoding
    (UTF-8, UTF-16, or ASCII) and does not contain binary control
    characters that are typical of binary formats.
    """
    # Common text BOMs.
    if header.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM
        return True
    if header.startswith(b"\xff\xfe") or header.startswith(b"\xfe\xff"):  # UTF-16 BOM
        return True

    # Read a larger sample for a more reliable heuristic.
    sample = header[:512]
    if len(sample) < 4:
        # Too short to tell — let extension-based detection handle it.
        return False

    printable = 0
    non_printable = 0
    for b in sample:
        if b == 0x00:
            # Null bytes are strong indicators of binary content.
            return False
        if 0x20 <= b <= 0x7E or b in (0x09, 0x0A, 0x0D):
            printable += 1
        elif b >= 0x80:
            # High bytes: may be UTF-8 continuation bytes (valid) or binary.
            # We count them as neutral.
            pass
        else:
            non_printable += 1

    total = printable + non_printable
    if total == 0:
        return False
    # Require at least 90% printable characters.
    return printable / total >= 0.9


def _read_text_head(path: Path, max_bytes: int = 4096) -> str | None:
    """Read the first *max_bytes* of a text file for format sniffing."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(max_bytes)
    except OSError:
        return None


def _guess_text_format(text: str, path: Path) -> SourceFormat | None:
    """Guess the text-based format from content markers.

    Returns ``None`` when the content is generic text with a recognised
    non-text extension (e.g. ``.html``) — in that case extension-based
    detection should take over.  For files without a recognised extension,
    defaults to ``TXT``.
    """
    lower = text.lower().lstrip()
    if lower.startswith("<!doctype html") or lower.startswith("<html"):
        return SourceFormat.HTML
    if lower.startswith("{\\rtf"):
        return SourceFormat.RTF
    if path.suffix.lower() in (".md", ".markdown"):
        return SourceFormat.MD
    if path.suffix.lower() in (".txt", ".text"):
        return SourceFormat.TXT
    # If the extension is one we recognise as a non-text format, defer to
    # extension-based detection to avoid misclassifying.
    if path.suffix.lower() in _EXTENSION_MAP:
        return None
    # Generic text with no recognised extension — treat as TXT.
    return SourceFormat.TXT


def _format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes} {unit}"
        size_bytes //= 1024
    return f"{size_bytes} TB"
