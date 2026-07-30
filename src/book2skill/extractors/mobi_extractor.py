"""MOBI/AZW extractor adapter for Book2Skill.

Wraps the selectively-ported Calibre ``ebook-convert`` backend from
virgiliojr94/book-to-skill. Requires the Calibre CLI (``ebook-convert``) on
PATH; produces a clear degradation message when unavailable. DRM-protected
files are detected via the PalmDOC encryption-type flag and refused before
extraction — Book2Skill never bypasses DRM.
"""

from __future__ import annotations

import hashlib
import re
import shutil
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
from book2skill.extractors._vendor.book_to_skill.calibre import (
    extract_with_ebook_convert,
)
from book2skill.extractors._vendor.book_to_skill.sanitize import sanitize_extracted_text
from book2skill.extractors.base import Extractor, ExtractorCapabilities

_SUPPORTED_SUFFIXES = {".mobi", ".azw", ".azw3"}


class MobiExtractor(Extractor):
    """Extract MOBI/AZW files via the Calibre ``ebook-convert`` CLI."""

    @property
    def name(self) -> str:
        return "book_to_skill.calibre"

    @property
    def version(self) -> str:
        return "1.1.0"

    def probe(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_SUFFIXES

    @property
    def capabilities(self) -> ExtractorCapabilities:
        return ExtractorCapabilities(requires_external_tool=True)

    def diagnostics(self) -> dict[str, bool]:
        return {"ebook-convert": self._calibre_available()}

    def extract_text_blocks(self, path: Path) -> list[TextBlock]:
        input_id = str(path)

        # DRM check first and independent of Calibre: this only reads the
        # PalmDOC header flag, never attempts decryption, so a DRM-protected
        # file is refused even when Calibre is unavailable (no-DRM-bypass rule).
        if self._looks_like_drm(path):
            raise DomainError(
                code=ErrorCode.GATE_ENCRYPTED_FILE,
                input_id=input_id,
                message=f"MOBI/AZW file appears DRM-protected: {path}",
                recovery="Book2Skill does not bypass DRM. Use a DRM-free copy.",
            )

        if not self._calibre_available():
            raise DomainError(
                code=ErrorCode.EXTRACT_UNSUPPORTED,
                input_id=input_id,
                message=(
                    "Calibre 'ebook-convert' not found on PATH; cannot "
                    f"extract MOBI/AZW: {path}"
                ),
                recovery=(
                    "Install Calibre (https://calibre-ebook.com) and ensure "
                    "'ebook-convert' is on PATH, or convert the file to a "
                    "supported format."
                ),
            )

        text = extract_with_ebook_convert(str(path))
        if text is None:
            raise DomainError(
                code=ErrorCode.GATE_DAMAGED_FILE,
                input_id=input_id,
                message=f"ebook-convert produced no text for MOBI/AZW: {path}",
                recovery=(
                    "Re-download or re-create the file and verify it opens "
                    "in Calibre."
                ),
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
            format=SourceFormat.MOBI,
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
    def _calibre_available() -> bool:
        """Return ``True`` when Calibre's ``ebook-convert`` is on PATH."""
        return shutil.which("ebook-convert") is not None

    @staticmethod
    def _looks_like_drm(path: Path) -> bool:
        """Heuristic DRM check via the PalmDOC encryption-type field.

        MOBI/AZW files are PalmDB (PDB) containers. Record 0 starts with a
        16-byte PalmDOC header whose encryption-type field (offset 12) is
        non-zero for DRM-protected books. This reads only the header flag —
        it never attempts decryption — so Book2Skill can refuse the file
        before invoking Calibre (per the no-DRM-bypass rule).

        Returns ``False`` for files too small or malformed to inspect, so
        that detection falls through to the normal damaged-file path.
        """
        try:
            data = path.read_bytes()
        except OSError:
            return False
        # PDB header: record count at offset 76 (2 bytes), record 0 offset
        # is the first entry of the record index at offset 78 (4 bytes).
        if len(data) < 82:
            return False
        n_records = int.from_bytes(data[76:78], "big")
        if n_records < 1:
            return False
        rec0_offset = int.from_bytes(data[78:82], "big")
        # PalmDOC encryption type is a 2-byte field at record0 + 12.
        if rec0_offset + 14 > len(data):
            return False
        enc_type = int.from_bytes(
            data[rec0_offset + 12 : rec0_offset + 14], "big"
        )
        return enc_type != 0

    @staticmethod
    def _paragraphs(text: str) -> list[str]:
        """Split Calibre txt output into paragraph blocks.

        ``ebook-convert`` to ``.txt`` separates paragraphs with blank lines;
        intra-paragraph soft wraps use single newlines. Split on runs of two
        or more newlines to recover paragraph boundaries.
        """
        normalized = text.replace("\r\n", "\n")
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", normalized)]
        return [p for p in paragraphs if p]
