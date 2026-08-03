"""Tests for the DOCX extractor adapter.

Builds minimal in-memory DOCX archives so the stdlib zip fallback and the
XML safety check are exercised without requiring ``python-docx``. Boundary
fixtures cover tables, mixed paragraphs/tables, corrupted inputs, empty
bodies, and probe/diagnostics contracts.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from book2skill.domain import DomainError, ErrorCode, SourceFormat
from book2skill.extractors._vendor.book_to_skill.docx import validate_docx_xml_safety
from book2skill.extractors._vendor.book_to_skill.exceptions import ExtractionError
from book2skill.extractors.base import ExtractorCapabilities
from book2skill.extractors.docx_extractor import DocxExtractor

_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _write_docx(path: Path, body_xml: str) -> None:
    """Write a minimal DOCX (zip with ``word/document.xml``) for *body_xml*."""
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_NS}"><w:body>{body_xml}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType='
            '"application/vnd.openxmlformats-package.relationships+xml"/>'
            '</Types>',
        )
        zf.writestr("word/document.xml", document)
    path.write_bytes(buf.getvalue())


def _build_docx(
    path: Path,
    paragraphs: list[str] | None = None,
    *,
    tables: list[list[list[str]]] | None = None,
    empty_body: bool = False,
) -> None:
    """Write a minimal DOCX with paragraphs followed by tables.

    Args:
        paragraphs: Paragraph texts in document order.
        tables: List of tables; each table is a list of rows; each row is a
            list of cell texts. Emitted after all paragraphs (zipfile backend
            preserves this order; python-docx appends tables last too).
        empty_body: If True, write a body with no paragraphs and no tables.
    """
    if empty_body:
        body = ""
    else:
        parts: list[str] = []
        for p in paragraphs or []:
            parts.append(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>')
        for table in tables or []:
            rows_xml = ""
            for row in table:
                cells_xml = "".join(
                    f'<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>'
                    for cell in row
                )
                rows_xml += f'<w:tr>{cells_xml}</w:tr>'
            parts.append(f'<w:tbl>{rows_xml}</w:tbl>')
        body = "".join(parts)
    _write_docx(path, body)


def _build_unsafe_docx(path: Path) -> None:
    """Write a DOCX whose XML declares a DTD (must be rejected)."""
    malicious = (
        '<!DOCTYPE foo [<!ENTITY xxe "attack">]>'
        '<w:document xmlns:w="' + _NS + '"><w:body/></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", malicious)
    path.write_bytes(buf.getvalue())


@pytest.fixture()
def extractor() -> DocxExtractor:
    return DocxExtractor()


def test_extractor_produces_paragraph_entries(
    tmp_path: Path, extractor: DocxExtractor
) -> None:
    source_path = tmp_path / "doc.docx"
    _build_docx(source_path, ["First paragraph.", "Second paragraph."])

    manifest, entries = extractor.extract(source_path, source_id="a" * 64)

    assert manifest.source_id == "a" * 64
    assert manifest.format == SourceFormat.DOCX
    assert manifest.extractor == "book_to_skill.docx"
    assert len(entries) == 2
    assert entries[0].block_id.endswith("-p1")
    assert entries[1].block_id.endswith("-p2")


def test_extractor_rejects_dtd_entity_declaration(
    tmp_path: Path, extractor: DocxExtractor
) -> None:
    source_path = tmp_path / "evil.docx"
    _build_unsafe_docx(source_path)

    with pytest.raises(ExtractionError, match="forbidden DTD"):
        extractor.extract(source_path, source_id="b" * 64)


def test_xml_safety_rejects_utf16_dtd_in_one_encoding_pass(tmp_path: Path) -> None:
    """The safety scan must recognize DTDs without trial-decoding every XML."""
    source_path = tmp_path / "utf16-evil.docx"
    payload = "<!DOCTYPE foo [<!ENTITY xxe 'attack'>]>".encode("utf-16le")
    with zipfile.ZipFile(source_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"\xff\xfe" + payload)

    with pytest.raises(ExtractionError, match="forbidden DTD"):
        validate_docx_xml_safety(str(source_path))


def test_xml_safety_applies_member_decompression_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oversized XML members are rejected before their bytes are read."""
    source_path = tmp_path / "oversized.docx"
    with zipfile.ZipFile(source_path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("word/document.xml", b"x" * 32)

    import book2skill.extractors._vendor.book_to_skill.docx as vendor_docx

    monkeypatch.setattr(vendor_docx, "_MAX_XML_MEMBER_BYTES", 16)
    with pytest.raises(ExtractionError, match="exceeds the XML safety budget"):
        validate_docx_xml_safety(str(source_path))


def test_extractor_rejects_missing_file(
    tmp_path: Path, extractor: DocxExtractor
) -> None:
    with pytest.raises(ExtractionError):
        extractor.extract(tmp_path / "missing.docx", source_id="c" * 64)


def test_extractor_preserves_table_rows(
    tmp_path: Path, extractor: DocxExtractor
) -> None:
    """Table rows must be extracted as tab-joined cell text.

    Both the python-docx and zipfile backends emit one tab-joined line per
    row, so this assertion holds under either backend.
    """
    source_path = tmp_path / "table.docx"
    _build_docx(
        source_path,
        tables=[[["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]],
    )

    blocks = extractor.extract_text_blocks(source_path)

    assert len(blocks) == 3
    assert blocks[0].text == "Name\tAge"
    assert blocks[1].text == "Alice\t30"
    assert blocks[2].text == "Bob\t25"


def test_extractor_handles_mixed_paragraphs_and_tables_in_document_order(
    tmp_path: Path, extractor: DocxExtractor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paragraphs and tables must be emitted in document order.

    The zipfile backend walks the body in order; python-docx collects all
    paragraphs first then all tables, so this test forces the zipfile backend
    with an interleaved body (paragraph → table → paragraph) to assert
    document order is preserved rather than reordered by element type.
    """
    # Interleaved body: paragraph, table row, paragraph — only an
    # order-preserving backend can keep the table between the two paragraphs.
    body = (
        '<w:p><w:r><w:t>Intro paragraph.</w:t></w:r></w:p>'
        '<w:tbl><w:tr>'
        '<w:tc><w:p><w:r><w:t>Cell A</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>Cell B</w:t></w:r></w:p></w:tc>'
        '</w:tr></w:tbl>'
        '<w:p><w:r><w:t>Closing paragraph.</w:t></w:r></w:p>'
    )
    source_path = tmp_path / "mixed.docx"
    _write_docx(source_path, body)

    import book2skill.extractors.docx_extractor as mod

    monkeypatch.setattr(mod, "extract_docx_with_python_docx", lambda _p: None)

    blocks = extractor.extract_text_blocks(source_path)

    # Document order: intro, table row, closing.
    assert [b.text for b in blocks] == [
        "Intro paragraph.",
        "Cell A\tCell B",
        "Closing paragraph.",
    ]


def test_extractor_rejects_bad_zip(
    tmp_path: Path, extractor: DocxExtractor
) -> None:
    """A non-ZIP file masquerading as .docx must be rejected at safety check."""
    source_path = tmp_path / "fake.docx"
    source_path.write_bytes(b"this is not a zip file")

    with pytest.raises(ExtractionError, match="Invalid DOCX file"):
        extractor.extract(source_path, source_id="d" * 64)


def test_extractor_rejects_empty_body(
    tmp_path: Path, extractor: DocxExtractor
) -> None:
    """A DOCX with an empty body must raise GATE_DAMAGED_FILE."""
    source_path = tmp_path / "empty.docx"
    _build_docx(source_path, empty_body=True)

    with pytest.raises(DomainError, match="Could not extract DOCX") as exc_info:
        extractor.extract(source_path, source_id="e" * 64)
    assert exc_info.value.code == ErrorCode.GATE_DAMAGED_FILE


def test_extractor_capabilities_and_diagnostics(extractor: DocxExtractor) -> None:
    caps = extractor.capabilities
    assert isinstance(caps, ExtractorCapabilities)
    assert caps.page_level is False
    assert caps.chapter_level is False
    diag = extractor.diagnostics()
    assert "python-docx" in diag


def test_extractor_probe(tmp_path: Path, extractor: DocxExtractor) -> None:
    assert extractor.probe(tmp_path / "doc.docx")
    assert not extractor.probe(tmp_path / "doc.pdf")
    assert not extractor.probe(tmp_path / "doc.txt")
