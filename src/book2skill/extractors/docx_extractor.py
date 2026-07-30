"""DOCX extractor adapter for Book2Skill.

Wraps the selectively-ported DOCX parser from virgiliojr94/book-to-skill.
Validates XML safety first, then prefers ``python-docx`` and falls back to
a stdlib zip-based reader.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from book2skill.domain import (
    ExtractionMapEntry,
    Locator,
    LocatorKind,
    SourceFormat,
    SourceManifest,
    TextBlock,
)
from book2skill.extractors._vendor.book_to_skill.docx import (
    extract_docx_with_python_docx,
    extract_docx_with_zipfile,
    validate_docx_xml_safety,
)
from book2skill.extractors._vendor.book_to_skill.sanitize import sanitize_extracted_text
from book2skill.extractors.base import Extractor, ExtractorCapabilities

_SUPPORTED_SUFFIXES = {".docx"}


class DocxExtractor(Extractor):
    """Extract DOCX files into paragraph-sized text blocks."""

    @property
    def name(self) -> str:
        return "book_to_skill.docx"

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

        return {"python-docx": importlib.util.find_spec("docx") is not None}

    def extract_text_blocks(self, path: Path) -> list[TextBlock]:
        # Reject DTD/entity declarations (Billion Laughs / XXE) before reading.
        validate_docx_xml_safety(str(path))

        text = extract_docx_with_python_docx(str(path))
        if not text or not text.strip():
            text = extract_docx_with_zipfile(str(path))
        if not text or not text.strip():
            from book2skill.domain import DomainError, ErrorCode

            raise DomainError(
                code=ErrorCode.GATE_DAMAGED_FILE,
                input_id=str(path),
                message=f"Could not extract DOCX file: {path}",
                recovery="The file may be corrupted or empty.",
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
            format=SourceFormat.DOCX,
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
        """Split extracted DOCX text into paragraph blocks.

        Both backends emit one line per paragraph / table row (joined with
        single newlines), so we split on runs of newlines.
        """
        normalized = text.replace("\r\n", "\n")
        paragraphs = [p.strip() for p in re.split(r"\n+", normalized)]
        return [p for p in paragraphs if p]
