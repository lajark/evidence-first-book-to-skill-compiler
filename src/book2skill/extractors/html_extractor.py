"""HTML extractor adapter for Book2Skill.

Wraps the selectively-ported HTML parser from virgiliojr94/book-to-skill.
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
from book2skill.extractors._vendor.book_to_skill.html import extract_html_file
from book2skill.extractors._vendor.book_to_skill.sanitize import sanitize_extracted_text
from book2skill.extractors.base import Extractor, ExtractorCapabilities

_SUPPORTED_SUFFIXES = {".html", ".htm"}


class HtmlExtractor(Extractor):
    """Extract HTML files into paragraph-sized text blocks."""

    @property
    def name(self) -> str:
        return "book_to_skill.html"

    @property
    def version(self) -> str:
        return "1.1.0"

    def probe(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_SUFFIXES

    @property
    def capabilities(self) -> ExtractorCapabilities:
        return ExtractorCapabilities()

    def diagnostics(self) -> dict[str, bool]:
        import importlib.util

        return {"beautifulsoup4": importlib.util.find_spec("bs4") is not None}

    def extract_text_blocks(self, path: Path) -> list[TextBlock]:
        if not path.exists():
            raise DomainError(
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                input_id=str(path),
                message=f"File not found: {path}",
                recovery="Check the path and try again.",
            )

        text = extract_html_file(str(path))
        if text is None:
            raise DomainError(
                code=ErrorCode.GATE_DAMAGED_FILE,
                input_id=str(path),
                message=f"Could not read HTML file: {path}",
                recovery="The file may be corrupted or not valid HTML.",
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
            format=SourceFormat.HTML,
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
        """Split extracted HTML text into paragraph blocks.

        HTML block elements are separated by single newlines (bs4
        ``separator="\\n"``) or per-tag newlines (stdlib fallback), so we split
        on runs of newlines rather than requiring blank-line separation.
        """
        normalized = text.replace("\r\n", "\n")
        paragraphs = [p.strip() for p in re.split(r"\n+", normalized)]
        return [p for p in paragraphs if p]
