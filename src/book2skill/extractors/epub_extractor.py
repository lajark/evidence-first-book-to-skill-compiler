"""EPUB extractor adapter for Book2Skill.

Wraps the selectively-ported EPUB parser from virgiliojr94/book-to-skill.
Falls back to a stdlib zip-based reader when ``ebooklib`` is unavailable.
"""

from __future__ import annotations

import hashlib
import re
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
from book2skill.extractors._vendor.book_to_skill.epub import (
    extract_with_ebooklib,
    extract_with_zipfile,
)
from book2skill.extractors._vendor.book_to_skill.sanitize import sanitize_extracted_text
from book2skill.extractors.base import Extractor, ExtractorCapabilities

_SUPPORTED_SUFFIXES = {".epub"}


class EpubExtractor(Extractor):
    """Extract EPUB files into per-chapter text blocks."""

    @property
    def name(self) -> str:
        return "book_to_skill.epub"

    @property
    def version(self) -> str:
        return "1.1.0"

    def probe(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_SUFFIXES

    @property
    def capabilities(self) -> ExtractorCapabilities:
        return ExtractorCapabilities(chapter_level=True)

    def diagnostics(self) -> dict[str, bool]:
        import importlib.util

        return {"ebooklib": importlib.util.find_spec("ebooklib") is not None}

    def extract_text_blocks(self, path: Path) -> list[TextBlock]:
        if not path.exists():
            raise DomainError(
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                input_id=str(path),
                message=f"File not found: {path}",
                recovery="Check the path and try again.",
            )

        # Prefer ebooklib; fall back to the stdlib zip reader. Both join
        # chapters with a blank line, so we recover chapter boundaries by
        # splitting on runs of two or more newlines.
        text = extract_with_ebooklib(str(path))
        if text is None:
            text = extract_with_zipfile(str(path))
        if text is None:
            raise DomainError(
                code=ErrorCode.GATE_DAMAGED_FILE,
                input_id=str(path),
                message=f"Could not extract EPUB file: {path}",
                recovery="The file may be corrupted or not a valid EPUB.",
            )

        sanitized_text, _removed = sanitize_extracted_text(text)
        return [
            TextBlock(
                text=chapter,
                locator=Locator(
                    kind=LocatorKind.CHAPTER,
                    page=None,
                    chapter=str(idx),
                    paragraph=None,
                ),
            )
            for idx, chapter in enumerate(self._chapters(sanitized_text), start=1)
            if chapter
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
            format=SourceFormat.EPUB,
            rights_confirmed=True,
            rights_note=rights_note,
            extractor=self.name,
            extractor_version=self.version,
            ingested_at=datetime.now(timezone.utc),
        )

        entries = [
            ExtractionMapEntry(
                block_id=f"{source_id}-c{idx}",
                source_id=source_id,
                text_sha256=hashlib.sha256(block.text.encode("utf-8")).hexdigest(),
                locator=block.locator,
                confidence=1.0,
            )
            for idx, block in enumerate(blocks, start=1)
        ]

        return manifest, entries

    @staticmethod
    def _chapters(text: str) -> list[str]:
        """Split extracted text into chapter blocks.

        Both backends join chapters with a blank line (``"\\n\\n"``) while
        intra-chapter block elements are separated by single newlines, so a
        run of two or more newlines reliably delimits chapters.
        """
        normalized = text.replace("\r\n", "\n")
        chapters = [c.strip() for c in re.split(r"\n{2,}", normalized)]
        return [c for c in chapters if c]
