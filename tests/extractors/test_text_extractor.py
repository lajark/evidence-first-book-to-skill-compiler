"""Tests for the TXT/Markdown extractor adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from book2skill.domain import DomainError, ErrorCode, SourceFormat
from book2skill.extractors.base import ExtractorCapabilities
from book2skill.extractors.text_extractor import TextExtractor


@pytest.fixture()
def extractor() -> TextExtractor:
    return TextExtractor()


def test_extractor_produces_manifest_and_entries(
    tmp_path: Path,
    extractor: TextExtractor,
) -> None:
    source_path = tmp_path / "notes.txt"
    source_path.write_text("Paragraph one.\n\nParagraph two.", encoding="utf-8")

    manifest, entries = extractor.extract(
        source_path,
        source_id="a" * 64,
    )

    assert manifest.source_id == "a" * 64
    assert manifest.format == SourceFormat.TXT
    assert manifest.extractor == "book_to_skill.text"
    assert manifest.content_sha256
    assert len(entries) == 2
    assert entries[0].block_id.endswith("-p1")
    assert entries[1].block_id.endswith("-p2")


def test_extractor_skips_blank_lines(tmp_path: Path, extractor: TextExtractor) -> None:
    source_path = tmp_path / "blank.txt"
    source_path.write_text("line one\n\n\nline two", encoding="utf-8")

    _manifest, entries = extractor.extract(
        source_path,
        source_id="b" * 64,
    )

    assert len(entries) == 2


def test_extractor_rejects_missing_file(
    tmp_path: Path,
    extractor: TextExtractor,
) -> None:
    with pytest.raises(DomainError, match="Could not read text file") as exc_info:
        extractor.extract(tmp_path / "missing.txt", source_id="c" * 64)
    assert exc_info.value.code == ErrorCode.GATE_FILE_NOT_FOUND


def test_extractor_reads_utf8_bom(tmp_path: Path, extractor: TextExtractor) -> None:
    source_path = tmp_path / "bom.txt"
    source_path.write_bytes(b"\xef\xbb\xbfHello BOM\n\nSecond paragraph.")

    _manifest, entries = extractor.extract(source_path, source_id="d" * 64)
    assert len(entries) == 2


def test_extractor_reads_gbk_chinese(tmp_path: Path, extractor: TextExtractor) -> None:
    """BOM-less GBK-encoded Chinese text must decode correctly."""
    source_path = tmp_path / "chinese.txt"
    content = "第一段落内容\n\n第二段落内容"
    source_path.write_bytes(content.encode("gbk"))

    _manifest, entries = extractor.extract(source_path, source_id="e" * 64)
    assert len(entries) == 2
    # Verify the text was decoded correctly (not mojibake).
    import hashlib

    expected_sha = hashlib.sha256("第一段落内容".encode()).hexdigest()
    assert entries[0].text_sha256 == expected_sha


def test_extractor_reads_gb2312_chinese(
    tmp_path: Path, extractor: TextExtractor
) -> None:
    """GB2312-encoded text (subset of GBK) must decode correctly."""
    source_path = tmp_path / "gb2312.txt"
    content = "测试文本\n\n第二段"
    source_path.write_bytes(content.encode("gb2312"))

    _manifest, entries = extractor.extract(source_path, source_id="f" * 64)
    assert len(entries) == 2


def test_extractor_detects_markdown_format(
    tmp_path: Path, extractor: TextExtractor
) -> None:
    source_path = tmp_path / "readme.md"
    source_path.write_text("# Title\n\nSome content.", encoding="utf-8")

    manifest, _entries = extractor.extract(source_path, source_id="g" * 64)
    assert manifest.format == SourceFormat.MD


def test_extractor_probe_txt(tmp_path: Path, extractor: TextExtractor) -> None:
    assert extractor.probe(tmp_path / "file.txt")
    assert extractor.probe(tmp_path / "file.md")
    assert extractor.probe(tmp_path / "file.markdown")
    assert not extractor.probe(tmp_path / "file.pdf")


def test_extractor_capabilities(extractor: TextExtractor) -> None:
    caps = extractor.capabilities
    assert isinstance(caps, ExtractorCapabilities)
    assert caps.page_level is False
    assert caps.chapter_level is False


def test_extractor_diagnostics(extractor: TextExtractor) -> None:
    # TextExtractor has no optional backends; diagnostics is empty by default.
    assert extractor.diagnostics() == {}


def test_extractor_preserves_markdown_headings(
    tmp_path: Path, extractor: TextExtractor
) -> None:
    """Markdown headings must be preserved verbatim as text blocks.

    The extractor does not parse Markdown structure — it splits on blank
    lines only — so heading syntax is kept as-is for downstream analysis.
    """
    source_path = tmp_path / "headings.md"
    source_path.write_text(
        "# Title\n\n## Section\n\n### Subsection",
        encoding="utf-8",
    )

    manifest, _ = extractor.extract(source_path, source_id="h" * 64)
    assert manifest.format == SourceFormat.MD

    blocks = extractor.extract_text_blocks(source_path)
    assert len(blocks) == 3
    assert blocks[0].text == "# Title"
    assert blocks[1].text == "## Section"
    assert blocks[2].text == "### Subsection"


def test_extractor_preserves_markdown_lists(
    tmp_path: Path, extractor: TextExtractor
) -> None:
    """Markdown list items must be preserved verbatim.

    List items separated by single newlines stay in a single block (the
    extractor splits on blank lines, not list markers); the literal marker
    syntax is retained for downstream analysis.
    """
    source_path = tmp_path / "lists.md"
    source_path.write_text(
        "- Item one\n- Item two\n- Item three",
        encoding="utf-8",
    )

    blocks = extractor.extract_text_blocks(source_path)

    assert len(blocks) == 1
    assert "- Item one" in blocks[0].text
    assert "- Item two" in blocks[0].text
    assert "- Item three" in blocks[0].text


def test_extractor_preserves_markdown_code_blocks(
    tmp_path: Path, extractor: TextExtractor
) -> None:
    """Fenced code blocks must be preserved verbatim, including the fence."""
    source_path = tmp_path / "code.md"
    source_path.write_text(
        "Intro text.\n\n```python\nprint('hello')\nx = 1\n```\n\nAfter code.",
        encoding="utf-8",
    )

    blocks = extractor.extract_text_blocks(source_path)

    # Three blocks separated by blank lines: intro, code fence, after.
    assert len(blocks) == 3
    assert blocks[0].text == "Intro text."
    assert "```python" in blocks[1].text
    assert "print('hello')" in blocks[1].text
    assert blocks[2].text == "After code."


def test_extractor_markdown_does_not_resolve_relative_links(
    tmp_path: Path, extractor: TextExtractor
) -> None:
    """Markdown relative links must be treated as literal text, never resolved.

    The extractor reads only the file passed to it; it must not open files
    referenced by Markdown links like ``[x](../secret.txt)``. This is the
    path-traversal defense: a malicious document cannot trick the extractor
    into reading files outside its scope.
    """
    # Place the .md in a subdirectory and the "secret" file one level up,
    # so a naive relative-link resolver would escape the subdirectory.
    sub_dir = tmp_path / "subdir"
    sub_dir.mkdir()
    secret_path = tmp_path / "secret.txt"
    secret_path.write_text("TOP SECRET CONTENT", encoding="utf-8")

    md_path = sub_dir / "doc.md"
    md_path.write_text(
        "# Title\n\nSee [secret](../secret.txt) for details.\n\nNormal paragraph.",
        encoding="utf-8",
    )

    blocks = extractor.extract_text_blocks(md_path)

    # The link target's contents must never appear in the extracted text;
    # only the literal Markdown source is preserved.
    for block in blocks:
        assert "TOP SECRET CONTENT" not in block.text
    # The literal link syntax is preserved as text.
    assert any("[secret](../secret.txt)" in block.text for block in blocks)


def test_extractor_handles_empty_file(
    tmp_path: Path, extractor: TextExtractor
) -> None:
    """An empty text file must produce an empty entry list, not an error."""
    source_path = tmp_path / "empty.txt"
    source_path.write_text("", encoding="utf-8")

    manifest, entries = extractor.extract(source_path, source_id="i" * 64)
    assert manifest.source_id == "i" * 64
    assert entries == []


def test_extractor_detects_markdown_extension_variants(
    tmp_path: Path, extractor: TextExtractor
) -> None:
    """Both .md and .markdown must be detected as Markdown format."""
    md_path = tmp_path / "a.md"
    md_path.write_text("# Title", encoding="utf-8")
    manifest_md, _ = extractor.extract(md_path, source_id="j" * 64)
    assert manifest_md.format == SourceFormat.MD

    markdown_path = tmp_path / "b.markdown"
    markdown_path.write_text("# Title", encoding="utf-8")
    manifest_markdown, _ = extractor.extract(markdown_path, source_id="k" * 64)
    assert manifest_markdown.format == SourceFormat.MD
