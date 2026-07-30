"""TXT / Markdown extractor adapter for Book2Skill.

Wraps the selectively-ported text reader from virgiliojr94/book-to-skill and
extends its encoding chain with GBK/GB2312 — essential for BOM-less Chinese
text files, which the upstream reader would otherwise mis-decode as cp1252.
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

# Byte-order marks, longest first: the UTF-32 LE BOM ("ff fe 00 00") starts
# with the UTF-16 LE BOM ("ff fe"), so UTF-32 must be checked before UTF-16.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)

# Fallback encoding chain for BOM-less files. GBK (a superset of GB2312) is
# tried before cp1252 so Chinese text is not silently mis-decoded as Western
# European mojibake.
_FALLBACK_ENCODINGS: tuple[str, ...] = (
    "utf-8",
    "gbk",
    "gb2312",
    "cp1252",
    "latin-1",
)

_SUPPORTED_SUFFIXES = {".txt", ".text", ".md", ".markdown"}


def read_text_with_gbk(path: str | Path) -> str:
    """Read a text file with BOM-aware encoding detection including GBK.

    Tries BOM detection first, then a fallback chain that inserts GBK/GB2312
    before cp1252 so BOM-less Chinese files decode correctly.
    """
    data = Path(path).read_bytes()

    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                break

    for encoding in _FALLBACK_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    # latin-1 never raises, so the loop above always returns. This line is
    # unreachable but keeps the type checker happy.
    return data.decode("latin-1", errors="replace")


class TextExtractor(Extractor):
    """Extract plain text and Markdown files.

    Splits the source into paragraph-sized blocks and produces a stable
    ``ExtractionMapEntry`` for each block.
    """

    @property
    def name(self) -> str:
        return "book_to_skill.text"

    @property
    def version(self) -> str:
        return "1.1.0"

    def probe(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_SUFFIXES

    @property
    def capabilities(self) -> ExtractorCapabilities:
        return ExtractorCapabilities()

    def extract_text_blocks(self, path: Path) -> list[TextBlock]:
        try:
            text = read_text_with_gbk(path)
        except OSError as exc:
            raise DomainError(
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                input_id=str(path),
                message=f"Could not read text file: {exc}",
                recovery="Check the file path and permissions.",
            ) from exc

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
            format=self._format_for(path),
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
    def _format_for(path: Path) -> SourceFormat:
        """Determine whether *path* is Markdown or plain text."""
        if path.suffix.lower() in (".md", ".markdown"):
            return SourceFormat.MD
        return SourceFormat.TXT

    @staticmethod
    def _paragraphs(text: str) -> list[str]:
        """Split text into paragraph blocks separated by blank lines."""
        normalized = text.replace("\r\n", "\n")
        paragraphs = [p.strip() for p in normalized.split("\n\n")]
        return [p for p in paragraphs if p]
