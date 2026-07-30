"""PDF extractor adapter for Book2Skill.

Wraps the selectively-ported PDF backends from virgiliojr94/book-to-skill and
adds a PyMuPDF backend (local addition) as the most reliable pure-Python
option. Backends are tried in order; the first non-empty result wins.

Encrypted PDFs are detected and rejected with a clear error code; image-only
(scanned) PDFs are flagged with an OCR-fallback hint.
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
from book2skill.extractors._vendor.book_to_skill.pdf import (
    count_pages,
    extract_with_docling,
    extract_with_pdfminer,
    extract_with_pdftotext,
    extract_with_pypdf,
)
from book2skill.extractors._vendor.book_to_skill.sanitize import sanitize_extracted_text
from book2skill.extractors.base import Extractor, ExtractorCapabilities


def extract_with_pymupdf(pdf_path: str) -> str | None:
    """Local addition: extract text with PyMuPDF (``fitz``).

    Not part of the upstream port; included because ``pymupdf`` is the
    declared optional dependency for the PDF group and is the most reliable
    pure-Python text extractor. Pages are separated by form-feed (``\\f``),
    which the wrapper splits on to recover page boundaries.
    """
    try:
        import fitz

        text_parts = []
        with fitz.open(pdf_path) as doc:
            for page in doc:
                text_parts.append(page.get_text() or "")
        return "\f".join(text_parts)
    except ImportError:
        return None
    except Exception:
        return None


# Backend chain: prefer the most reliable pure-Python option, then fall back
# through CLI and other libraries. Each returns None when unavailable.
_BACKENDS = (
    extract_with_pymupdf,
    extract_with_pdftotext,
    extract_with_pypdf,
    extract_with_pdfminer,
    extract_with_docling,
)


class PdfExtractor(Extractor):
    """Extract PDF files into per-page text blocks."""

    @property
    def name(self) -> str:
        return "book_to_skill.pdf"

    @property
    def version(self) -> str:
        return "1.1.0"

    def probe(self, path: Path) -> bool:
        if path.suffix.lower() == ".pdf":
            return True
        # Magic-byte check for extensionless files.
        try:
            with open(path, "rb") as fh:
                return fh.read(5) == b"%PDF-"
        except OSError:
            return False

    @property
    def capabilities(self) -> ExtractorCapabilities:
        return ExtractorCapabilities(page_level=True)

    def diagnostics(self) -> dict[str, bool]:
        """Report which PDF backends are available on this machine."""
        import importlib.util
        import shutil

        available: dict[str, bool] = {}
        for module in ("fitz", "pypdf", "pdfminer"):
            try:
                available[module] = importlib.util.find_spec(module) is not None
            except ModuleNotFoundError:
                available[module] = False
        available["pdftotext"] = bool(shutil.which("pdftotext"))
        return available

    def extract_text_blocks(self, path: Path) -> list[TextBlock]:
        if not path.exists():
            raise DomainError(
                code=ErrorCode.GATE_FILE_NOT_FOUND,
                input_id=str(path),
                message=f"File not found: {path}",
                recovery="Check the path and try again.",
            )

        # Reject encrypted PDFs before attempting extraction.
        if self._is_encrypted(path):
            raise DomainError(
                code=ErrorCode.GATE_ENCRYPTED_FILE,
                input_id=str(path),
                message=f"PDF is encrypted: {path}",
                recovery=(
                    "Decrypt the PDF first. DRM removal is not supported; "
                    "only process PDFs you can legally open."
                ),
            )

        try:
            text = self._extract_text(path)
        except Exception as exc:
            raise DomainError(
                code=ErrorCode.GATE_DAMAGED_FILE,
                input_id=str(path),
                message=f"Could not read PDF file: {exc}",
                recovery=(
                    "The PDF may be corrupted. "
                    "Try re-downloading or re-creating it."
                ),
            ) from exc

        if text is None or not text.strip():
            # No text recovered — likely a scanned/image-only PDF.
            # Attempt OCR fallback if Tesseract is available (P1).
            from book2skill.extractors.ocr_backend import (
                INSTALL_HINT,
                extract_text_from_pdf,
                is_available,
            )

            if is_available():
                try:
                    ocr_text = extract_text_from_pdf(path)
                except Exception as exc:
                    raise DomainError(
                        code=ErrorCode.GATE_DAMAGED_FILE,
                        input_id=str(path),
                        message=f"OCR fallback failed: {exc}",
                        recovery=(
                            "The PDF may be corrupted. Try re-creating it "
                            "or use a different OCR tool."
                        ),
                    ) from exc
                if ocr_text.strip():
                    text = ocr_text
                else:
                    raise DomainError(
                        code=ErrorCode.GATE_DAMAGED_FILE,
                        input_id=str(path),
                        message=(
                            f"No text extracted from PDF "
                            f"(OCR also found no text): {path}"
                        ),
                        recovery=(
                            "The PDF may contain only non-text images. "
                            "Verify the file is a valid document."
                        ),
                    )
            else:
                raise DomainError(
                    code=ErrorCode.GATE_DAMAGED_FILE,
                    input_id=str(path),
                    message=(
                        f"No text extracted from PDF "
                        f"(possibly scanned/image-only): {path}"
                    ),
                    recovery=INSTALL_HINT,
                )

        sanitized_text, _removed = sanitize_extracted_text(text)
        return [
            TextBlock(
                text=page_text,
                locator=Locator(
                    kind=LocatorKind.PAGE,
                    page=idx,
                    paragraph=None,
                ),
            )
            for idx, page_text in enumerate(self._pages(sanitized_text), start=1)
            if page_text
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
            format=SourceFormat.PDF,
            rights_confirmed=True,
            rights_note=rights_note,
            extractor=self.name,
            extractor_version=self.version,
            ingested_at=datetime.now(timezone.utc),
        )

        entries = [
            ExtractionMapEntry(
                block_id=f"{source_id}-pg{idx}",
                source_id=source_id,
                text_sha256=hashlib.sha256(block.text.encode("utf-8")).hexdigest(),
                locator=block.locator,
                confidence=1.0,
            )
            for idx, block in enumerate(blocks, start=1)
        ]

        return manifest, entries

    @staticmethod
    def _extract_text(path: Path) -> str | None:
        for backend in _BACKENDS:
            text = backend(str(path))
            if text and text.strip():
                return text
        return None

    @staticmethod
    def _is_encrypted(path: Path) -> bool:
        """Detect whether *path* is an encrypted PDF.

        Tries PyMuPDF first, then pypdf. Returns ``False`` when neither
        backend is available (the extraction attempt will then surface the
        underlying error).
        """
        try:
            import fitz

            doc = fitz.open(str(path))
            encrypted = doc.is_encrypted
            doc.close()
            return bool(encrypted)
        except ImportError:
            pass
        except Exception:
            pass

        try:
            import pypdf

            with open(path, "rb") as fh:
                return bool(pypdf.PdfReader(fh).is_encrypted)
        except Exception:
            return False

    @staticmethod
    def _pages(text: str) -> list[str]:
        """Split extracted text into page blocks.

        PyMuPDF and pdftotext separate pages with a form-feed (``\\f``);
        pypdf/pdfminer join pages with newlines and lose page boundaries, in
        which case we fall back to a single block so the text is not lost.
        """
        normalized = text.replace("\r\n", "\n")
        if "\f" in normalized:
            pages = [p.strip() for p in normalized.split("\f")]
            return [p for p in pages if p]
        stripped = normalized.strip()
        return [stripped] if stripped else []


def has_pdf_backend() -> bool:
    """Return True if any PDF text backend is importable on this machine."""
    import importlib.util
    import shutil

    # Check top-level package names only; importing a submodule name via
    # ``find_spec`` can raise ``ModuleNotFoundError`` when the parent package
    # is absent, so guard each probe.
    for module in ("fitz", "pypdf", "pdfminer"):
        try:
            if importlib.util.find_spec(module) is not None:
                return True
        except ModuleNotFoundError:
            continue
    return bool(shutil.which("pdftotext"))


# Keep ``count_pages`` re-exported for callers that need a page count without
# extracting full text (e.g. progress reporting).
__all__ = ["PdfExtractor", "extract_with_pymupdf", "has_pdf_backend", "count_pages"]
