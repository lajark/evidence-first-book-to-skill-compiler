"""Optional OCR backend for scanned / image-only PDFs (PRD P1).

When a PDF yields no text from the regular text-extraction backends
(PyMuPDF / pdftotext / pypdf / pdfminer), this module provides an OCR
fallback via Tesseract (:mod:`pytesseract` + :mod:`PIL`).

Tesseract is **not** a hard dependency — mirroring the Calibre adapter
pattern. When the optional packages or the ``tesseract`` binary are absent,
:func:`is_available` returns ``False`` and callers fall back to the
``GATE_DAMAGED_FILE`` error with installation guidance.

Design notes
------------

- PDF pages are rendered to 300 DPI PNG images via PyMuPDF
  (:meth:`fitz.Document.get_pixmap`), then passed to
  :func:`pytesseract.image_to_string`.
- The DPI is configurable; 300 is a sane default that balances accuracy and
  speed.
- Only PDFs are supported; OCR for EPUB/DOCX images is out of scope.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

#: Default render resolution (dots per inch). Higher = better OCR accuracy
#: but slower; 300 is the common standard for document OCR.
_DEFAULT_DPI = 300


def is_available() -> bool:
    """Return ``True`` when both ``pytesseract`` and the tesseract binary exist.

    Checks the Python package *and* the system binary because pytesseract
    raises at call time if the binary is missing.
    """
    if importlib.util.find_spec("pytesseract") is None:
        return False
    if importlib.util.find_spec("PIL") is None:
        return False
    # Tesseract binary must be on PATH.
    return shutil.which("tesseract") is not None


def diagnostics() -> dict[str, bool]:
    """Report which OCR components are available."""
    return {
        "pytesseract": importlib.util.find_spec("pytesseract") is not None,
        "PIL": importlib.util.find_spec("PIL") is not None,
        "tesseract_binary": shutil.which("tesseract") is not None,
        "fitz": importlib.util.find_spec("fitz") is not None,
    }


def extract_text_from_pdf(path: Path, *, dpi: int = _DEFAULT_DPI) -> str:
    """OCR a PDF and return concatenated page text.

    Renders each page to a PNG image via PyMuPDF, then runs Tesseract on
    each image. Pages are separated by form-feed (``\\f``) to mirror
    pdftotext's convention.

    Raises:
        ImportError: If ``pytesseract``, ``PIL``, or ``fitz`` is missing.
        RuntimeError: If Tesseract fails on a specific page.
    """
    import fitz
    import pytesseract  # type: ignore[import-not-found]
    from PIL import Image  # type: ignore[import-not-found]

    zoom = dpi / 72.0  # PyMuPDF uses 72 DPI as the base.
    doc = fitz.open(str(path))
    pages: list[str] = []
    try:
        for page in doc:
            matrix = fitz.Matrix(zoom, zoom)
            pixmap = page.get_pixmap(matrix=matrix)
            img_data = pixmap.tobytes("png")
            img = Image.open(__import__("io").BytesIO(img_data))
            text = pytesseract.image_to_string(img)
            pages.append(text)
    finally:
        doc.close()

    return "\f".join(pages)


#: Human-readable recovery hint shown when OCR is unavailable.
INSTALL_HINT = (
    "Install Tesseract OCR for scanned-PDF support: "
    "pip install pytesseract Pillow (and the tesseract binary). "
    "On Windows: choco install tesseract; on macOS: brew install tesseract; "
    "on Linux: apt install tesseract-ocr."
)


__all__ = [
    "is_available",
    "diagnostics",
    "extract_text_from_pdf",
    "INSTALL_HINT",
]
