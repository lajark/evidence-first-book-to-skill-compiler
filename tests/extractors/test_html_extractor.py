"""Tests for the HTML extractor adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from book2skill.domain import DomainError, ErrorCode, SourceFormat
from book2skill.extractors.html_extractor import HtmlExtractor


@pytest.fixture()
def extractor() -> HtmlExtractor:
    return HtmlExtractor()


def test_extractor_strips_script_and_style(
    tmp_path: Path, extractor: HtmlExtractor
) -> None:
    source_path = tmp_path / "page.html"
    source_path.write_text(
        "<html><head><title>x</title></head>"
        "<body><script>var a=1;</script>"
        "<p>Hello world.</p><style>.a{}</style>"
        "<p>Second paragraph.</p></body></html>",
        encoding="utf-8",
    )

    manifest, entries = extractor.extract(source_path, source_id="a" * 64)

    assert manifest.source_id == "a" * 64
    assert manifest.format == SourceFormat.HTML
    assert manifest.extractor == "book_to_skill.html"
    assert len(entries) == 2
    assert entries[0].block_id.endswith("-p1")
    assert entries[1].block_id.endswith("-p2")


def test_extractor_rejects_missing_file(
    tmp_path: Path, extractor: HtmlExtractor
) -> None:
    with pytest.raises(DomainError, match="File not found") as exc_info:
        extractor.extract(tmp_path / "missing.html", source_id="b" * 64)
    assert exc_info.value.code == ErrorCode.GATE_FILE_NOT_FOUND


def test_extractor_preserves_headings_and_lists(
    tmp_path: Path, extractor: HtmlExtractor
) -> None:
    """Headings and list items must be preserved in the extracted text."""
    source_path = tmp_path / "structured.html"
    source_path.write_text(
        "<html><body>"
        "<h1>Title</h1>"
        "<p>Intro paragraph.</p>"
        "<ul><li>First item</li><li>Second item</li></ul>"
        "</body></html>",
        encoding="utf-8",
    )

    blocks = extractor.extract_text_blocks(source_path)

    # All visible text must be preserved somewhere in the blocks.
    combined = "\n".join(b.text for b in blocks)
    assert "Title" in combined
    assert "Intro paragraph." in combined
    assert "First item" in combined
    assert "Second item" in combined


def test_extractor_capabilities_and_diagnostics(extractor: HtmlExtractor) -> None:
    from book2skill.extractors.base import ExtractorCapabilities

    caps = extractor.capabilities
    assert isinstance(caps, ExtractorCapabilities)
    assert caps.page_level is False
    assert caps.chapter_level is False
    diag = extractor.diagnostics()
    assert "beautifulsoup4" in diag


def test_extractor_probe(tmp_path: Path, extractor: HtmlExtractor) -> None:
    assert extractor.probe(tmp_path / "page.html")
    assert extractor.probe(tmp_path / "page.htm")
    assert not extractor.probe(tmp_path / "page.txt")
    assert not extractor.probe(tmp_path / "page.pdf")
