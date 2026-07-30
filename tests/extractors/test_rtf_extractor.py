"""Tests for the RTF extractor adapter."""

from __future__ import annotations

import pytest

from book2skill.domain import DomainError, ErrorCode, SourceFormat
from book2skill.extractors.rtf_extractor import RtfExtractor, rtf_to_text


class TestRtfToText:
    """Unit tests for the RTF-to-text conversion function."""

    def test_basic_paragraph(self) -> None:
        raw = rb"{\rtf1\ansi Hello world!\par}"
        assert rtf_to_text(raw) == "Hello world!\n"

    def test_multiple_paragraphs(self) -> None:
        raw = rb"{\rtf1\ansi First paragraph.\par\par Second paragraph.\par}"
        text = rtf_to_text(raw)
        assert "First paragraph." in text
        assert "Second paragraph." in text

    def test_bold_and_italic_stripped(self) -> None:
        raw = rb"{\rtf1\ansi \b Bold text\b0 and \i italic\i0 .\par}"
        text = rtf_to_text(raw)
        assert "Bold text" in text
        assert "italic" in text
        assert "\\b" not in text

    def test_ignorable_destination_skipped(self) -> None:
        raw = (
            b"{\\rtf1\\ansi"
            b"{\\*\\fonttbl {\\f0 Times New Roman;}}"
            b"\\f0 Visible text.\\par}"
        )
        text = rtf_to_text(raw)
        assert "Times New Roman" not in text
        assert "Visible text." in text

    def test_hex_escape(self) -> None:
        raw = rb"{\rtf1\ansi \'e9l\'e8ve\par}"
        text = rtf_to_text(raw)
        assert "é" in text
        assert "è" in text

    def test_literal_braces(self) -> None:
        raw = rb"{\rtf1\ansi \{literal\} and \\backslash\par}"
        text = rtf_to_text(raw)
        assert "{" in text
        assert "}" in text
        assert "\\" in text

    def test_tab(self) -> None:
        raw = rb"{\rtf1\ansi col1\tab col2\par}"
        text = rtf_to_text(raw)
        assert "col1" in text
        assert "col2" in text

    def test_line_break(self) -> None:
        raw = rb"{\rtf1\ansi Line one\line line two\par}"
        text = rtf_to_text(raw)
        assert "Line one" in text
        assert "line two" in text

    def test_unicode_escape(self) -> None:
        raw = rb"{\rtf1\ansi \u12345X\par}"
        text = rtf_to_text(raw)
        assert "?" in text

    def test_nested_groups(self) -> None:
        raw = (
            b"{\\rtf1\\ansi"
            b"{\\b Nested {\\i bold-italic} text}"
            b"\\par}"
        )
        text = rtf_to_text(raw)
        assert "Nested" in text
        assert "bold-italic" in text
        assert "text" in text

    def test_non_breaking_space(self) -> None:
        raw = rb"{\rtf1\ansi word1\~word2\par}"
        text = rtf_to_text(raw)
        assert " " in text  # non-breaking space

    def test_empty_rtf(self) -> None:
        raw = b"{\\rtf1\\ansi}"
        text = rtf_to_text(raw)
        assert text.strip() == ""

    def test_soft_hyphen(self) -> None:
        raw = rb"{\rtf1\ansi soft\_hyphen\par}"
        text = rtf_to_text(raw)
        assert "‑" in text


class TestRtfExtractor:
    """Integration tests for the RtfExtractor adapter."""

    @pytest.fixture
    def extractor(self) -> RtfExtractor:
        return RtfExtractor()

    def test_extract_basic(self, extractor: RtfExtractor, tmp_path):
        source_path = tmp_path / "test.rtf"
        source_path.write_bytes(
            b"{\\rtf1\\ansi\\deff0"
            b"{\\fonttbl {\\f0 Times New Roman;}}"
            b"\\f0\\fs24 First paragraph.\\par"
            b"\\par"
            b"Second paragraph with \\b bold\\b0 text.\\par"
            b"}"
        )

        manifest, entries = extractor.extract(source_path, source_id="a" * 64)

        assert manifest.format == SourceFormat.RTF
        assert manifest.extractor == "book_to_skill.rtf"
        assert len(entries) >= 1
        blocks = extractor.extract_text_blocks(source_path)
        assert len(blocks) >= 1
        all_text = " ".join(b.text for b in blocks)
        assert "First paragraph" in all_text
        assert "bold" in all_text

    def test_extract_text_blocks(self, extractor: RtfExtractor, tmp_path) -> None:
        source_path = tmp_path / "doc.rtf"
        source_path.write_bytes(
            b"{\\rtf1\\ansi Paragraph one.\\par\\par Paragraph two.\\par}"
        )

        blocks = extractor.extract_text_blocks(source_path)
        assert len(blocks) >= 1

    def test_missing_file(self, extractor: RtfExtractor, tmp_path) -> None:
        with pytest.raises(DomainError) as exc:
            extractor.extract_text_blocks(tmp_path / "missing.rtf")
        assert exc.value.code == ErrorCode.GATE_FILE_NOT_FOUND

    def test_probe(self, extractor: RtfExtractor, tmp_path) -> None:
        assert extractor.probe(tmp_path / "doc.rtf")
        assert not extractor.probe(tmp_path / "doc.pdf")
        assert not extractor.probe(tmp_path / "doc.txt")

    def test_diagnostics(self, extractor: RtfExtractor) -> None:
        diag = extractor.diagnostics()
        assert "stdlib_rtf_parser" in diag
        assert diag["stdlib_rtf_parser"] is True

    def test_capabilities(self, extractor: RtfExtractor) -> None:
        caps = extractor.capabilities
        assert caps is not None

    def test_name_and_version(self, extractor: RtfExtractor) -> None:
        assert extractor.name == "book_to_skill.rtf"
        assert extractor.version == "1.0.0"

    def test_empty_rtf_fails(self, extractor: RtfExtractor, tmp_path) -> None:
        source_path = tmp_path / "empty.rtf"
        source_path.write_bytes(b"{\\rtf1\\ansi}")

        with pytest.raises(DomainError) as exc:
            extractor.extract_text_blocks(source_path)
        assert exc.value.code == ErrorCode.GATE_DAMAGED_FILE

    def test_extract_with_manifest_original_name(
        self, extractor: RtfExtractor, tmp_path
    ) -> None:
        source_path = tmp_path / "test.rtf"
        source_path.write_bytes(b"{\\rtf1\\ansi Hello world.\\par}")

        manifest, _ = extractor.extract(
            source_path,
            source_id="b" * 64,
            original_name="custom_name.rtf",
            rights_note="Test note",
        )
        assert manifest.original_name == "custom_name.rtf"
        assert manifest.rights_note == "Test note"
        assert manifest.rights_confirmed is True
