"""Format → extractor registry.

Maps each supported :class:`~book2skill.domain.SourceFormat` to the
:class:`~book2skill.extractors.base.Extractor` that handles it. The
pipeline resolves an extractor by format (after the gate has determined
it) and can also probe an unknown file to find a matching adapter.
"""

from __future__ import annotations

from pathlib import Path

from book2skill.domain import SourceFormat
from book2skill.extractors.base import Extractor
from book2skill.extractors.docx_extractor import DocxExtractor
from book2skill.extractors.epub_extractor import EpubExtractor
from book2skill.extractors.html_extractor import HtmlExtractor
from book2skill.extractors.mobi_extractor import MobiExtractor
from book2skill.extractors.pdf_extractor import PdfExtractor
from book2skill.extractors.rtf_extractor import RtfExtractor
from book2skill.extractors.text_extractor import TextExtractor


class ExtractorRegistry:
    """Registry of extractors keyed by source format.

    A single extractor instance may be registered for multiple formats
    (e.g. ``TextExtractor`` serves both ``TXT`` and ``MD``).
    """

    def __init__(self) -> None:
        self._extractors: dict[SourceFormat, Extractor] = {}

    def register(self, fmt: SourceFormat, extractor: Extractor) -> None:
        """Register *extractor* as the handler for *fmt*.

        A later registration for the same format silently replaces the
        earlier one, which supports override/test-double injection.
        """
        self._extractors[fmt] = extractor

    def get(self, fmt: SourceFormat) -> Extractor | None:
        """Return the extractor for *fmt*, or ``None`` if unregistered."""
        return self._extractors.get(fmt)

    def require(self, fmt: SourceFormat) -> Extractor:
        """Return the extractor for *fmt*, raising ``LookupError`` if absent."""
        extractor = self._extractors.get(fmt)
        if extractor is None:
            raise LookupError(f"No extractor registered for format: {fmt.value}")
        return extractor

    def formats(self) -> list[SourceFormat]:
        """Return the sorted list of registered formats."""
        return sorted(self._extractors, key=lambda f: f.value)

    def probe(self, path: Path) -> Extractor | None:
        """Find the first registered extractor whose ``probe`` accepts *path*.

        Iteration order follows :meth:`formats` (sorted by format value).
        Returns ``None`` when no extractor claims the file.
        """
        for fmt in self.formats():
            extractor = self._extractors[fmt]
            if extractor.probe(path):
                return extractor
        return None


def default_registry() -> ExtractorRegistry:
    """Build and return a registry with all built-in extractors registered.

    P0 formats (PDF, EPUB, DOCX, TXT, MD, HTML) and P1 formats
    (MOBI, AZW, AZW3, RTF) are all registered.
    """
    text = TextExtractor()
    mobi = MobiExtractor()
    registry = ExtractorRegistry()
    registry.register(SourceFormat.TXT, text)
    registry.register(SourceFormat.MD, text)
    registry.register(SourceFormat.PDF, PdfExtractor())
    registry.register(SourceFormat.EPUB, EpubExtractor())
    registry.register(SourceFormat.DOCX, DocxExtractor())
    registry.register(SourceFormat.HTML, HtmlExtractor())
    registry.register(SourceFormat.RTF, RtfExtractor())
    registry.register(SourceFormat.MOBI, mobi)
    registry.register(SourceFormat.AZW, mobi)
    registry.register(SourceFormat.AZW3, mobi)
    return registry


__all__ = ["ExtractorRegistry", "default_registry"]
