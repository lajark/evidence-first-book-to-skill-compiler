"""Tests for the PDF extractor adapter.

Extraction tests require a PDF backend (PyMuPDF / pypdf / pdfminer /
``pdftotext``). When none is installed the page test is skipped; the
missing-file and encrypted tests always run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from book2skill.domain import DomainError, ErrorCode, LocatorKind, SourceFormat
from book2skill.extractors.base import ExtractorCapabilities
from book2skill.extractors.pdf_extractor import PdfExtractor, has_pdf_backend

# Minimal valid PDF containing one page with the text "Hello world".
# Built as a byte template so no third-party PDF writer is required.
_MINIMAL_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 50 700 Td (Hello world) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000360 00000 n
trailer
<< /Size 6 /Root 1 0 R >>
startxref
444
%%EOF
"""


@pytest.fixture()
def extractor() -> PdfExtractor:
    return PdfExtractor()


def test_extractor_rejects_missing_file(
    tmp_path: Path, extractor: PdfExtractor
) -> None:
    with pytest.raises(DomainError, match="File not found") as exc_info:
        extractor.extract(tmp_path / "missing.pdf", source_id="a" * 64)
    assert exc_info.value.code == ErrorCode.GATE_FILE_NOT_FOUND


def test_extractor_rejects_scanned_or_empty_pdf(
    tmp_path: Path, extractor: PdfExtractor
) -> None:
    """A valid-structure PDF with no text should raise a scanned-PDF error."""
    source_path = tmp_path / "empty.pdf"
    source_path.write_bytes(_MINIMAL_PDF.replace(b"Hello world", b""))

    with pytest.raises(DomainError, match="No text extracted") as exc_info:
        extractor.extract(source_path, source_id="b" * 64)
    assert exc_info.value.code == ErrorCode.GATE_DAMAGED_FILE
    assert "OCR" in exc_info.value.recovery


def test_extractor_rejects_thin_text_from_multipage_scanned_pdf(
    tmp_path: Path, extractor: PdfExtractor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cover-page text layer must not mask an otherwise scanned book."""
    source_path = tmp_path / "mostly-scanned.pdf"
    source_path.write_bytes(b"%PDF-1.4\nplaceholder")
    monkeypatch.setattr(
        PdfExtractor,
        "_extract_text",
        staticmethod(lambda _path: "Title and publisher metadata only."),
    )
    monkeypatch.setattr(
        "book2skill.extractors.pdf_extractor.count_pages", lambda _path: 100
    )
    monkeypatch.setattr(
        "book2skill.extractors.ocr_backend.is_available", lambda: False
    )

    with pytest.raises(DomainError, match="insufficient") as exc_info:
        extractor.extract(source_path, source_id="d" * 64)

    assert exc_info.value.code == ErrorCode.GATE_DAMAGED_FILE
    assert "OCR" in exc_info.value.recovery


def _can_create_encrypted_pdf() -> bool:
    """Check whether PyMuPDF is available for creating encrypted test PDFs."""
    try:
        import fitz  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(
    not _can_create_encrypted_pdf(),
    reason="PyMuPDF required to create encrypted test PDF",
)
def test_extractor_rejects_encrypted_pdf(
    tmp_path: Path, extractor: PdfExtractor
) -> None:
    """An encrypted PDF must be rejected with GATE_ENCRYPTED_FILE."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Secret content")
    source_path = tmp_path / "encrypted.pdf"
    doc.save(
        str(source_path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    doc.close()

    with pytest.raises(DomainError, match="encrypted") as exc_info:
        extractor.extract(source_path, source_id="e" * 64)
    assert exc_info.value.code == ErrorCode.GATE_ENCRYPTED_FILE
    assert "DRM" in exc_info.value.recovery


@pytest.mark.skipif(
    not has_pdf_backend(),
    reason="no PDF text backend installed (pymupdf/pypdf/pdfminer/pdftotext)",
)
def test_extractor_produces_page_entries(
    tmp_path: Path, extractor: PdfExtractor
) -> None:
    source_path = tmp_path / "doc.pdf"
    source_path.write_bytes(_MINIMAL_PDF)

    manifest, entries = extractor.extract(source_path, source_id="a" * 64)

    assert manifest.source_id == "a" * 64
    assert manifest.format == SourceFormat.PDF
    assert manifest.extractor == "book_to_skill.pdf"
    assert len(entries) >= 1
    assert entries[0].locator.kind == "page"
    assert entries[0].locator.page == 1


def test_fallback_without_page_boundaries_does_not_invent_page_numbers(
    tmp_path: Path, extractor: PdfExtractor, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "fallback.pdf"
    source_path.write_bytes(b"%PDF-1.4\nplaceholder")
    monkeypatch.setattr(
        PdfExtractor,
        "_extract_text",
        staticmethod(lambda _path: "Text extracted without page separators."),
    )

    manifest, entries = extractor.extract(source_path, source_id="c" * 64)

    assert manifest.format == SourceFormat.PDF
    assert len(entries) == 1
    assert entries[0].locator.kind == LocatorKind.UNKNOWN
    assert entries[0].locator.page is None
    assert entries[0].block_id.endswith("-b1")


def test_extractor_probe_pdf(tmp_path: Path, extractor: PdfExtractor) -> None:
    assert extractor.probe(tmp_path / "doc.pdf")

    # Magic-byte check for extensionless files.
    magic_file = tmp_path / "noext"
    magic_file.write_bytes(b"%PDF-1.4")
    assert extractor.probe(magic_file)

    assert not extractor.probe(tmp_path / "doc.txt")
    assert not extractor.probe(tmp_path / "doc.bin")


def test_extractor_capabilities(extractor: PdfExtractor) -> None:
    caps = extractor.capabilities
    assert isinstance(caps, ExtractorCapabilities)
    assert caps.page_level is True
    assert caps.chapter_level is False


def test_extractor_diagnostics(extractor: PdfExtractor) -> None:
    diag = extractor.diagnostics()
    assert "fitz" in diag
    assert "pypdf" in diag
    assert "pdfminer" in diag
    assert "pdftotext" in diag
    # At least one backend should be available in the test environment.
    assert any(diag.values())
