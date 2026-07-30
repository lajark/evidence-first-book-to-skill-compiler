"""RTF extractor adapter for Book2Skill.

Extracts plain text from Rich Text Format files by stripping RTF control
words, groups, and destinations while preserving paragraph structure.

No external dependencies are required — the parser is a self-contained
state machine that handles the subset of RTF needed for text extraction.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from book2skill.domain import (
    DomainError,
    ErrorCode,
    ExtractionMapEntry,
    Locator,
    LocatorKind,
    SourceFormat,
    SourceManifest,
    TextBlock,
)
from book2skill.extractors._vendor.book_to_skill.sanitize import sanitize_extracted_text
from book2skill.extractors.base import Extractor, ExtractorCapabilities

_SUPPORTED_SUFFIXES = {".rtf"}

#: Standard RTF destinations whose content is metadata, not document text.
#: These are always skipped (the ``\\*`` ignorable prefix is handled
#: separately via the skip-stack in :func:`rtf_to_text`).
_SKIP_DESTINATIONS: frozenset[str] = frozenset(
    {
        "fonttbl",
        "stylesheet",
        "colortbl",
        "info",
        "pict",
        "header",
        "footer",
        "footerpar",
        "headerpar",
        "listtable",
        "listoverridetable",
        "rsidtbl",
        "latentstyles",
    }
)


def rtf_to_text(raw: bytes) -> str:
    """Strip RTF markup and return plain text.

    Handles the subset of RTF needed for text extraction:

    - Control words (``\\word``, ``\\word123``) — stripped
    - Groups (``{...}``) — nested groups are tracked; ``\\*`` destinations
      are skipped entirely
    - ``\\par``, ``\\line`` → newline
    - ``\\tab`` → space
    - ``\\'hh`` hex escapes → decoded
    - ``\\{``, ``\\}``, ``\\\\`` → literal characters
    - Unicode escapes ``\\u12345`` → replaced with ``?`` (simplified)

    Returns:
        Plain text with paragraph breaks preserved.
    """
    # Decode as ASCII superset; RTF is 7-bit ASCII with optional escapes.
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    result: list[str] = []
    i = 0
    n = len(text)
    # Stack of destination-group flags; True = skip content.
    skip_stack: list[bool] = [False]

    def _skipping() -> bool:
        return any(skip_stack)

    while i < n:
        ch = text[i]

        if ch == "{":
            # Check if next char is \* (ignorable destination).
            peek = i + 1
            if peek < n and text[peek] == "\\":
                peek2 = peek + 1
                if peek2 < n and text[peek2] == "*":
                    skip_stack.append(True)
                    i = peek2 + 1
                    continue
            skip_stack.append(_skipping())
            i += 1
            continue

        if ch == "}":
            if len(skip_stack) > 1:
                skip_stack.pop()
            i += 1
            continue

        if ch == "\\":
            if _skipping():
                i = _skip_control_word(text, i)
                continue

            # Check for standard destinations (fonttbl, colortbl, …) that
            # should be skipped even without the \* ignorable prefix.
            dest = _peek_control_word(text, i)
            if dest in _SKIP_DESTINATIONS:
                skip_stack.append(True)
                i = _skip_control_word(text, i)
                continue

            i = _handle_escape(text, i, n, result)
            continue

        if not _skipping():
            result.append(ch)
        i += 1

    return "".join(result)


def _skip_control_word(text: str, i: int) -> int:
    """Advance past a control word (``\\word`` or ``\\word123``)."""
    i += 1  # skip backslash
    n = len(text)
    while i < n and text[i].isalpha():
        i += 1
    # Optional numeric parameter (possibly negative).
    if i < n and text[i] == "-":
        i += 1
    while i < n and text[i].isdigit():
        i += 1
    # Optional space delimiter (not part of text).
    if i < n and text[i] == " ":
        i += 1
    return i


def _peek_control_word(text: str, i: int) -> str:
    """Return the control word name at position *i* without advancing.

    *i* points at the leading ``\\``. Returns the alphabetic control word
    (e.g. ``"fonttbl"``) or ``""`` if the sequence is not a control word.
    """
    j = i + 1  # skip backslash
    n = len(text)
    start = j
    while j < n and text[j].isalpha():
        j += 1
    return text[start:j]


def _handle_escape(text: str, i: int, n: int, result: list[str]) -> int:
    """Handle an RTF escape sequence starting at ``\\``.

    Returns the new index after consuming the escape.
    """
    i += 1  # skip backslash
    if i >= n:
        result.append("\\")
        return i

    ch = text[i]

    # Hex escape: \'hh
    if ch == "'" and i + 2 < n:
        try:
            codepoint = int(text[i + 1 : i + 3], 16)
            result.append(chr(codepoint))
        except ValueError:
            pass
        return i + 3

    # Unicode escape: \uN (simplified — drop the codepoint).
    if ch == "u" and i + 1 < n:
        j = i + 1
        if j < n and text[j] == "-":
            j += 1
        while j < n and text[j].isdigit():
            j += 1
        # Skip trailing space + optional \'hh replacement.
        if j < n and text[j] == " ":
            j += 1
        if j + 3 < n and text[j] == "\\" and text[j + 1] == "'":
            j += 4
        result.append("?")
        return j

    # Literal escapes.
    if ch in ("\\", "{", "}"):
        result.append(ch)
        return i + 1
    if ch == "~":
        result.append(" ")  # non-breaking space
        return i + 1
    if ch == "_":
        result.append("‑")  # non-breaking hyphen
        return i + 1

    # Control symbols.
    if ch == "\n" or ch == "\r":
        # Escaped newline — skip it (line continuation).
        if ch == "\r" and i + 1 < n and text[i + 1] == "\n":
            return i + 2
        return i + 1

    # Control words we translate to text.
    rest = text[i:]
    if rest.startswith("par") and (i + 3 >= n or not text[i + 3].isalpha()):
        result.append("\n")
        return _skip_control_word(text, i - 1)  # re-use the skip logic
    if rest.startswith("line") and (i + 4 >= n or not text[i + 4].isalpha()):
        result.append("\n")
        return _skip_control_word(text, i - 1)
    if rest.startswith("tab") and (i + 3 >= n or not text[i + 3].isalpha()):
        result.append(" ")
        return _skip_control_word(text, i - 1)

    # Generic control word — skip.
    return _skip_control_word(text, i - 1)


class RtfExtractor(Extractor):
    """Extract plain text from RTF files."""

    @property
    def name(self) -> str:
        return "book_to_skill.rtf"

    @property
    def version(self) -> str:
        return "1.0.0"

    def probe(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_SUFFIXES

    @property
    def capabilities(self) -> ExtractorCapabilities:
        return ExtractorCapabilities()

    def diagnostics(self) -> dict[str, bool]:
        return {"stdlib_rtf_parser": True}

    def extract_text_blocks(self, path: Path) -> list[TextBlock]:
        if not path.exists():
            raise DomainError(
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                input_id=str(path),
                message=f"File not found: {path}",
                recovery="Check the path and try again.",
            )

        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise DomainError(
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                input_id=str(path),
                message=f"Could not read RTF file: {exc}",
                recovery="Check the file path and permissions.",
            ) from exc

        text = rtf_to_text(raw)
        if not text.strip():
            raise DomainError(
                code=ErrorCode.GATE_DAMAGED_FILE,
                input_id=str(path),
                message=f"RTF file produced no extractable text: {path}",
                recovery="The file may be empty or contain only images/objects.",
            )

        sanitized_text, _removed = sanitize_extracted_text(text)
        return [
            TextBlock(
                text=paragraph,
                locator=Locator(
                    kind=LocatorKind.PARAGRAPH,
                    page=None,
                    paragraph=idx,
                ),
            )
            for idx, paragraph in enumerate(self._paragraphs(sanitized_text), start=1)
            if paragraph
        ]

    def extract(
        self,
        path: Path,
        *,
        source_id: str,
        version: int = 1,
        original_name: str | None = None,
        rights_note: str | None = None,
    ) -> tuple[SourceManifest, list[ExtractionMapEntry]]:
        blocks = self.extract_text_blocks(path)

        raw = path.read_bytes()
        content_sha256 = hashlib.sha256(raw).hexdigest()

        manifest = SourceManifest(
            source_id=source_id,
            version=version,
            original_name=original_name or path.name,
            content_sha256=content_sha256,
            format=SourceFormat.RTF,
            rights_confirmed=True,
            rights_note=rights_note,
            extractor=self.name,
            extractor_version=self.version,
            ingested_at=datetime.now(timezone.utc),
        )

        entries = [
            ExtractionMapEntry(
                block_id=f"{source_id}-p{idx}",
                source_id=source_id,
                text_sha256=hashlib.sha256(block.text.encode("utf-8")).hexdigest(),
                locator=block.locator,
                confidence=1.0,
            )
            for idx, block in enumerate(blocks, start=1)
        ]

        return manifest, entries

    @staticmethod
    def _paragraphs(text: str) -> list[str]:
        """Split text into paragraph blocks separated by blank lines."""
        normalized = text.replace("\r\n", "\n")
        paragraphs = [p.strip() for p in normalized.split("\n\n")]
        return [p for p in paragraphs if p]
