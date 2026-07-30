"""Tests for the optional OCR backend (PRD P1 OCR fallback).

Tesseract is not installed in the test environment, so these tests mock
the OCR functions to verify the integration points without requiring the
binary.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from book2skill.domain import DomainError, ErrorCode
from book2skill.extractors import ocr_backend
from book2skill.extractors.ocr_backend import (
    INSTALL_HINT,
    diagnostics,
    is_available,
)
from book2skill.extractors.pdf_extractor import PdfExtractor


class TestAvailability:
    """is_available / diagnostics behaviour."""

    def test_is_available_returns_bool(self) -> None:
        assert isinstance(is_available(), bool)

    def test_diagnostics_returns_dict(self) -> None:
        diag = diagnostics()
        assert "pytesseract" in diag
        assert "PIL" in diag
        assert "tesseract_binary" in diag
        assert "fitz" in diag
        for v in diag.values():
            assert isinstance(v, bool)

    def test_install_hint_is_nonempty(self) -> None:
        assert "Tesseract" in INSTALL_HINT
        assert "pip install" in INSTALL_HINT


def _make_scanned_pdf(tmp_path: Path, name: str = "scanned.pdf") -> Path:
    """Write a minimal PDF stub that is not encrypted."""
    pdf_path = tmp_path / name
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return pdf_path


class TestPdfExtractorOcrFallback:
    """Integration: PdfExtractor uses OCR when no text is found."""

    def test_ocr_fallback_attempted_on_empty_text(
        self, tmp_path: Path
    ) -> None:
        """When a PDF has no text, the extractor attempts OCR if available."""
        pdf_path = _make_scanned_pdf(tmp_path)
        extractor = PdfExtractor()

        with (
            patch.object(PdfExtractor, "_extract_text", return_value=""),
            patch.object(ocr_backend, "is_available", return_value=True),
            patch.object(
                ocr_backend,
                "extract_text_from_pdf",
                return_value="OCR extracted text from page 1.",
            ),
        ):
            blocks = extractor.extract_text_blocks(pdf_path)

        assert len(blocks) >= 1
        assert "OCR extracted text" in blocks[0].text

    def test_ocr_unavailable_provides_install_hint(
        self, tmp_path: Path
    ) -> None:
        """When OCR is unavailable, the error includes install instructions."""
        pdf_path = _make_scanned_pdf(tmp_path, "bad.pdf")
        extractor = PdfExtractor()

        with (
            patch.object(PdfExtractor, "_extract_text", return_value=""),
            patch.object(ocr_backend, "is_available", return_value=False),
            pytest.raises(DomainError) as exc,
        ):
            extractor.extract_text_blocks(pdf_path)

        assert exc.value.code == ErrorCode.GATE_DAMAGED_FILE
        assert (
            "Tesseract" in exc.value.recovery
            or "pip install" in exc.value.recovery
        )

    def test_ocr_finds_no_text_raises_error(self, tmp_path: Path) -> None:
        """When OCR also returns no text, a clear error is raised."""
        pdf_path = _make_scanned_pdf(tmp_path, "empty.pdf")
        extractor = PdfExtractor()

        with (
            patch.object(PdfExtractor, "_extract_text", return_value=""),
            patch.object(ocr_backend, "is_available", return_value=True),
            patch.object(
                ocr_backend, "extract_text_from_pdf", return_value=""
            ),
            pytest.raises(DomainError) as exc,
        ):
            extractor.extract_text_blocks(pdf_path)

        assert exc.value.code == ErrorCode.GATE_DAMAGED_FILE
        assert "OCR also found no text" in exc.value.message

    def test_ocr_failure_raises_damaged_error(self, tmp_path: Path) -> None:
        """When OCR raises an exception, it's wrapped as GATE_DAMAGED_FILE."""
        pdf_path = _make_scanned_pdf(tmp_path, "crash.pdf")
        extractor = PdfExtractor()

        with (
            patch.object(PdfExtractor, "_extract_text", return_value=""),
            patch.object(ocr_backend, "is_available", return_value=True),
            patch.object(
                ocr_backend,
                "extract_text_from_pdf",
                side_effect=RuntimeError("tesseract crashed"),
            ),
            pytest.raises(DomainError) as exc,
        ):
            extractor.extract_text_blocks(pdf_path)

        assert exc.value.code == ErrorCode.GATE_DAMAGED_FILE
        assert "OCR fallback failed" in exc.value.message
