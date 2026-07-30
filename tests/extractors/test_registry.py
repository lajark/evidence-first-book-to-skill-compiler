"""Tests for the extractor registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from book2skill.domain import SourceFormat
from book2skill.extractors import (
    DocxExtractor,
    EpubExtractor,
    ExtractorRegistry,
    HtmlExtractor,
    MobiExtractor,
    PdfExtractor,
    RtfExtractor,
    TextExtractor,
    default_registry,
)


class TestRegistryBasics:
    """Tests for ``ExtractorRegistry`` register/get/formats."""

    def test_register_and_get(self) -> None:
        reg = ExtractorRegistry()
        ext = TextExtractor()
        reg.register(SourceFormat.TXT, ext)
        assert reg.get(SourceFormat.TXT) is ext

    def test_get_unregistered_returns_none(self) -> None:
        reg = ExtractorRegistry()
        assert reg.get(SourceFormat.TXT) is None

    def test_require_unregistered_raises(self) -> None:
        reg = ExtractorRegistry()
        with pytest.raises(LookupError):
            reg.require(SourceFormat.TXT)

    def test_require_registered_returns_extractor(self) -> None:
        reg = ExtractorRegistry()
        ext = TextExtractor()
        reg.register(SourceFormat.TXT, ext)
        assert reg.require(SourceFormat.TXT) is ext

    def test_formats_sorted(self) -> None:
        reg = ExtractorRegistry()
        reg.register(SourceFormat.PDF, PdfExtractor())
        reg.register(SourceFormat.TXT, TextExtractor())
        formats = reg.formats()
        assert formats == sorted(formats, key=lambda f: f.value)
        assert SourceFormat.PDF in formats
        assert SourceFormat.TXT in formats

    def test_replace_on_re_register(self) -> None:
        reg = ExtractorRegistry()
        first = TextExtractor()
        second = TextExtractor()
        reg.register(SourceFormat.TXT, first)
        reg.register(SourceFormat.TXT, second)
        assert reg.get(SourceFormat.TXT) is second

    def test_empty_registry_formats(self) -> None:
        reg = ExtractorRegistry()
        assert reg.formats() == []


class TestRegistryProbe:
    """Tests for ``ExtractorRegistry.probe``."""

    def test_probe_finds_pdf_extractor(self, tmp_path: Path) -> None:
        reg = ExtractorRegistry()
        reg.register(SourceFormat.PDF, PdfExtractor())
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        result = reg.probe(f)
        assert result is not None
        assert isinstance(result, PdfExtractor)

    def test_probe_finds_text_extractor(self, tmp_path: Path) -> None:
        reg = ExtractorRegistry()
        reg.register(SourceFormat.TXT, TextExtractor())
        f = tmp_path / "notes.txt"
        f.write_text("hello")
        result = reg.probe(f)
        assert result is not None
        assert isinstance(result, TextExtractor)

    def test_probe_returns_none_for_unsupported(self, tmp_path: Path) -> None:
        reg = ExtractorRegistry()
        reg.register(SourceFormat.TXT, TextExtractor())
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02")
        assert reg.probe(f) is None

    def test_probe_empty_registry(self, tmp_path: Path) -> None:
        reg = ExtractorRegistry()
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")
        assert reg.probe(f) is None


class TestDefaultRegistry:
    """Tests for ``default_registry``."""

    def test_all_p0_formats_registered(self) -> None:
        reg = default_registry()
        for fmt in (
            SourceFormat.PDF,
            SourceFormat.EPUB,
            SourceFormat.DOCX,
            SourceFormat.TXT,
            SourceFormat.MD,
            SourceFormat.HTML,
        ):
            assert reg.get(fmt) is not None, f"{fmt.value} should be registered"

    def test_mobi_formats_registered(self) -> None:
        reg = default_registry()
        for fmt in (SourceFormat.MOBI, SourceFormat.AZW, SourceFormat.AZW3):
            assert reg.get(fmt) is not None

    def test_rtf_is_registered(self) -> None:
        reg = default_registry()
        assert isinstance(reg.get(SourceFormat.RTF), RtfExtractor)

    def test_text_extractor_shares_txt_and_md(self) -> None:
        reg = default_registry()
        txt_ext = reg.get(SourceFormat.TXT)
        md_ext = reg.get(SourceFormat.MD)
        assert txt_ext is md_ext
        assert isinstance(txt_ext, TextExtractor)

    def test_mobi_extractor_shares_mobi_azw_azw3(self) -> None:
        reg = default_registry()
        mobi_ext = reg.get(SourceFormat.MOBI)
        azw_ext = reg.get(SourceFormat.AZW)
        azw3_ext = reg.get(SourceFormat.AZW3)
        assert mobi_ext is azw_ext is azw3_ext
        assert isinstance(mobi_ext, MobiExtractor)

    def test_correct_extractor_types(self) -> None:
        reg = default_registry()
        assert isinstance(reg.get(SourceFormat.PDF), PdfExtractor)
        assert isinstance(reg.get(SourceFormat.EPUB), EpubExtractor)
        assert isinstance(reg.get(SourceFormat.DOCX), DocxExtractor)
        assert isinstance(reg.get(SourceFormat.HTML), HtmlExtractor)

    def test_default_registry_can_probe_pdf(self, tmp_path: Path) -> None:
        reg = default_registry()
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        result = reg.probe(f)
        assert isinstance(result, PdfExtractor)

    def test_default_registry_can_probe_md(self, tmp_path: Path) -> None:
        reg = default_registry()
        f = tmp_path / "readme.md"
        f.write_text("# Title")
        result = reg.probe(f)
        assert isinstance(result, TextExtractor)
