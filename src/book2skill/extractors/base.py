"""Base extractor interface.

Defines the contract every format adapter must satisfy: probing a file,
extracting its content into normalized text blocks with source locators,
declaring its capabilities, and reporting optional backend availability.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from book2skill.domain import (
    ExtractionMapEntry,
    SourceFormat,
    SourceManifest,
    TextBlock,
    derive_block_id,
)


class ExtractionContractError(ValueError):
    """Raised when an adapter returns semantically inconsistent extraction data."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(self.issues))


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


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """One-pass extraction output consumed by application use cases."""

    manifest: SourceManifest
    blocks: tuple[TextBlock, ...]
    entries: tuple[ExtractionMapEntry, ...]

    def __post_init__(self) -> None:
        if len(self.blocks) != len(self.entries):
            raise ValueError("each extracted block must have one map entry")


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

    def extract_result(
        self,
        path: Path,
        *,
        source_id: str,
        source_format: SourceFormat,
        content_sha256: str | None = None,
        version: int = 1,
        original_name: str | None = None,
        rights_note: str | None = None,
    ) -> ExtractionResult:
        """Extract blocks, manifest and source map in one adapter pass.

        This additive API preserves the original two-item :meth:`extract`
        contract for SDK callers. Custom extractors may override it when they
        need specialized map metadata; the default owns IDs and hashes in
        Core and calls :meth:`extract_text_blocks` exactly once.
        """
        blocks = tuple(self.extract_text_blocks(path))
        digest = content_sha256 or _sha256_path(path)
        manifest = SourceManifest(
            source_id=source_id,
            version=version,
            original_name=original_name or path.name,
            content_sha256=digest,
            format=source_format,
            rights_confirmed=True,
            rights_note=rights_note,
            extractor=self.name,
            extractor_version=self.version,
            ingested_at=datetime.now(timezone.utc),
        )
        entries = tuple(
            ExtractionMapEntry(
                block_id=derive_block_id(source_id, block.locator, idx),
                source_id=source_id,
                text_sha256=hashlib.sha256(block.text.encode("utf-8")).hexdigest(),
                locator=block.locator,
                confidence=1.0,
            )
            for idx, block in enumerate(blocks, start=1)
        )
        return ExtractionResult(manifest=manifest, blocks=blocks, entries=entries)

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


def validate_extraction_result(
    result: ExtractionResult,
    *,
    source_id: str,
    source_format: SourceFormat,
    content_sha256: str | None = None,
) -> None:
    """Validate the semantic contract shared by every extractor.

    Pydantic validates individual records, but it cannot verify relationships
    across the manifest, text blocks and source map.  This gate runs before
    Raw persistence and before any LLM call, so an adapter cannot silently
    publish stale, mislabelled or untraceable content.
    """
    issues: list[str] = []
    manifest = result.manifest
    blocks = tuple(result.blocks)
    entries = tuple(result.entries)

    if manifest.source_id != source_id:
        issues.append("manifest.source_id does not match the trusted source_id")
    if manifest.format != source_format:
        issues.append("manifest.format does not match the detected source format")
    if content_sha256 is not None and manifest.content_sha256 != content_sha256:
        issues.append("manifest.content_sha256 does not match the gated file hash")
    if not blocks:
        issues.append("extraction returned no text blocks")
    if len(blocks) != len(entries):
        issues.append("each extracted block must have one map entry")

    seen_block_ids: set[str] = set()
    for ordinal, (block, entry) in enumerate(
        zip(blocks, entries, strict=True), start=1
    ):
        if not block.text.strip():
            issues.append(f"block {ordinal} contains only whitespace")
        if entry.source_id != source_id:
            issues.append(f"entry {ordinal} source_id does not match the source")
        if entry.locator != block.locator:
            issues.append(f"entry {ordinal} locator does not match its block")

        expected_hash = hashlib.sha256(block.text.encode("utf-8")).hexdigest()
        if entry.text_sha256 != expected_hash:
            issues.append(f"entry {ordinal} text_sha256 does not match its block")

        expected_block_id = derive_block_id(source_id, block.locator, ordinal)
        if entry.block_id != expected_block_id:
            issues.append(
                f"entry {ordinal} block_id is not the deterministic "
                "source-scoped identifier"
            )
        if entry.block_id in seen_block_ids:
            issues.append(f"duplicate block_id: {entry.block_id}")
        seen_block_ids.add(entry.block_id)

    if issues:
        raise ExtractionContractError(issues)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ExtractionContractError",
    "ExtractionResult",
    "Extractor",
    "ExtractorCapabilities",
    "validate_extraction_result",
]
