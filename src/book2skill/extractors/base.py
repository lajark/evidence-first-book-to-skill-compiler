"""Base extractor interface.

Defines the contract every format adapter must satisfy: probing a file,
extracting its content into normalized text blocks with source locators,
declaring its capabilities, and reporting optional backend availability.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from book2skill.domain import ExtractionMapEntry, SourceManifest, TextBlock


@dataclass(frozen=True, slots=True)
class ExtractorCapabilities:
    """Declares what an extractor can recover from a source.

    Used by the pipeline to set expectations (e.g. whether page-level
    locators will be available) and to choose fallbacks.
    """

    page_level: bool = False
    """Can the extractor recover page boundaries?"""

    chapter_level: bool = False
    """Can the extractor recover chapter/section boundaries?"""

    ocr_fallback: bool = False
    """Does the extractor have an OCR fallback for image-only sources?"""

    requires_external_tool: bool = False
    """Does the extractor require an external CLI tool (e.g. Calibre)?"""


class Extractor(ABC):
    """Abstract base class for source extractors.

    Each extractor is responsible for converting a raw source file into a
    ``SourceManifest`` and a list of ``ExtractionMapEntry`` records.
    """

    @abstractmethod
    def extract(
        self,
        path: Path,
        *,
        source_id: str,
        version: int = 1,
        original_name: str | None = None,
        rights_note: str | None = None,
    ) -> tuple[SourceManifest, list[ExtractionMapEntry]]:
        """Extract a source file.

        Args:
            path: Path to the source file.
            source_id: Stable content-derived identifier.
            version: Version number for this source.
            original_name: Optional original filename override.
            rights_note: Optional rights confirmation note.

        Returns:
            A tuple of ``(SourceManifest, list[ExtractionMapEntry])``.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable extractor name."""
        ...

    @abstractmethod
    def extract_text_blocks(self, path: Path) -> list[TextBlock]:
        """Extract sanitized text blocks with their locators.

        This is the core extraction primitive: ``extract`` builds its
        ``ExtractionMapEntry`` records from the blocks returned here, and
        downstream use cases (e.g. Analyze) use this method to access the
        actual text content.
        """
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Extractor version."""
        ...

    def probe(self, path: Path) -> bool:
        """Return ``True`` if this extractor can likely handle *path*.

        The default implementation accepts any file — subclasses should
        override with an extension or magic-byte check so the registry can
        auto-select the right adapter.
        """
        return True

    @property
    def capabilities(self) -> ExtractorCapabilities:
        """Return this extractor's capabilities.

        Subclasses override to declare page/chapter/OCR support.
        """
        return ExtractorCapabilities()

    def diagnostics(self) -> dict[str, bool]:
        """Report availability of optional backends or external tools.

        Returns a mapping of ``{backend_name: available}``. The default
        implementation reports no optional backends; subclasses override to
        expose their dependency status (e.g. Calibre, PyMuPDF).
        """
        return {}
