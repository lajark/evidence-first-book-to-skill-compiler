"""Tests for the EPUB extractor adapter.

Builds minimal in-memory EPUB archives so the stdlib zip fallback is
exercised without requiring ``ebooklib``. Boundary fixtures cover spine
ordering, single-chapter books, corrupted inputs, and partial success when
a spine-referenced file is missing from the archive.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from book2skill.domain import DomainError, ErrorCode, SourceFormat
from book2skill.extractors.base import ExtractorCapabilities
from book2skill.extractors.epub_extractor import EpubExtractor


def _build_epub(
    path: Path,
    *,
    spine: tuple[str, ...] = ("ch1", "ch2"),
    chapters: dict[str, str] | None = None,
    omit_files: set[str] | None = None,
) -> None:
    """Write a minimal valid-shape EPUB to *path*.

    Args:
        spine: Ordered idrefs in the OPF spine (controls reading order).
        chapters: Map of item id -> XHTML body inner content. Defaults to
            two chapters with distinguishing text.
        omit_files: Set of chapter hrefs (e.g. ``"ch2.xhtml"``) to omit
            from the archive while keeping them in the spine, simulating a
            broken EPUB that references missing files.
    """
    if chapters is None:
        chapters = {
            "ch1": "<p>Chapter one text.</p>",
            "ch2": "<p>Chapter two text.</p>",
        }
    omit_files = omit_files or set()

    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    manifest_items = "".join(
        f'<item id="{item_id}" href="{item_id}.xhtml" '
        f'media-type="application/xhtml+xml"/>'
        for item_id in chapters
    )
    spine_items = "".join(f'<itemref idref="{idref}"/>' for idref in spine)
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-id="bookid">'
        f'<manifest>{manifest_items}</manifest>'
        f'<spine>{spine_items}</spine>'
        '</package>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        for item_id, body in chapters.items():
            href = f"{item_id}.xhtml"
            if href in omit_files:
                continue
            zf.writestr(
                f"OEBPS/{href}",
                f"<html><body>{body}</body></html>",
            )
    path.write_bytes(buf.getvalue())


@pytest.fixture()
def extractor() -> EpubExtractor:
    return EpubExtractor()


def test_extractor_produces_chapter_entries(
    tmp_path: Path, extractor: EpubExtractor
) -> None:
    source_path = tmp_path / "book.epub"
    _build_epub(source_path)

    manifest, entries = extractor.extract(source_path, source_id="a" * 64)

    assert manifest.source_id == "a" * 64
    assert manifest.format == SourceFormat.EPUB
    assert manifest.extractor == "book_to_skill.epub"
    assert len(entries) == 2
    assert entries[0].block_id.endswith("-c1")
    assert entries[1].block_id.endswith("-c2")
    assert entries[0].locator.kind == "chapter"


def test_extractor_rejects_missing_file(
    tmp_path: Path, extractor: EpubExtractor
) -> None:
    with pytest.raises(DomainError, match="File not found") as exc_info:
        extractor.extract(tmp_path / "missing.epub", source_id="b" * 64)
    assert exc_info.value.code == ErrorCode.GATE_FILE_NOT_FOUND


def test_extractor_preserves_spine_order(
    tmp_path: Path, extractor: EpubExtractor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spine order — not manifest order — determines chapter sequence.

    The zipfile backend reads the OPF spine to build reading order. The
    ebooklib backend's iteration order over ITEM_DOCUMENT is not contractually
    tied to the spine, so this test forces the zipfile backend to make the
    assertion deterministic.
    """
    source_path = tmp_path / "reordered.epub"
    _build_epub(
        source_path,
        spine=("ch2", "ch1"),
        chapters={
            "ch1": "<p>Alpha chapter.</p>",
            "ch2": "<p>Beta chapter.</p>",
        },
    )
    # Force the stdlib zip backend so spine order is authoritative.
    import book2skill.extractors.epub_extractor as mod

    monkeypatch.setattr(mod, "extract_with_ebooklib", lambda _p: None)

    blocks = extractor.extract_text_blocks(source_path)

    assert len(blocks) == 2
    # Spine is (ch2, ch1): Beta must come first.
    assert blocks[0].text == "Beta chapter."
    assert blocks[1].text == "Alpha chapter."


def test_extractor_handles_single_chapter(
    tmp_path: Path, extractor: EpubExtractor
) -> None:
    """A single-chapter EPUB must produce exactly one entry."""
    source_path = tmp_path / "single.epub"
    _build_epub(
        source_path,
        spine=("ch1",),
        chapters={"ch1": "<p>Only chapter.</p>"},
    )

    _manifest, entries = extractor.extract(source_path, source_id="c" * 64)

    assert len(entries) == 1
    assert entries[0].block_id.endswith("-c1")


def test_extractor_rejects_non_zip_file(
    tmp_path: Path, extractor: EpubExtractor
) -> None:
    """A non-ZIP file masquerading as .epub must raise GATE_DAMAGED_FILE."""
    source_path = tmp_path / "fake.epub"
    source_path.write_bytes(b"this is not a zip file")

    with pytest.raises(DomainError, match="Could not extract EPUB") as exc_info:
        extractor.extract(source_path, source_id="d" * 64)
    assert exc_info.value.code == ErrorCode.GATE_DAMAGED_FILE


def test_extractor_rejects_epub_without_opf(
    tmp_path: Path, extractor: EpubExtractor
) -> None:
    """A valid ZIP without an OPF or any HTML content cannot be extracted."""
    source_path = tmp_path / "noopf.epub"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("README.txt", "not an epub")
    source_path.write_bytes(buf.getvalue())

    with pytest.raises(DomainError, match="Could not extract EPUB") as exc_info:
        extractor.extract(source_path, source_id="e" * 64)
    assert exc_info.value.code == ErrorCode.GATE_DAMAGED_FILE


def test_extractor_partial_success_skips_missing_chapter(
    tmp_path: Path, extractor: EpubExtractor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spine referencing a missing file must still yield the chapters that
    ARE present — single-file failure must not lose the whole batch.

    The zipfile backend swallows per-file read errors and continues; we force
    that backend to make the assertion deterministic.
    """
    source_path = tmp_path / "broken.epub"
    _build_epub(
        source_path,
        spine=("ch1", "ch2"),
        chapters={
            "ch1": "<p>Chapter one text.</p>",
            "ch2": "<p>Chapter two text.</p>",
        },
        omit_files={"ch2.xhtml"},  # ch2 referenced by spine but absent from zip
    )
    import book2skill.extractors.epub_extractor as mod

    monkeypatch.setattr(mod, "extract_with_ebooklib", lambda _p: None)

    _manifest, entries = extractor.extract(source_path, source_id="f" * 64)

    # Only ch1 is recoverable; ch2 is missing but ch1 must not be lost.
    assert len(entries) == 1
    assert entries[0].block_id.endswith("-c1")


def test_extractor_capabilities_and_diagnostics(extractor: EpubExtractor) -> None:
    caps = extractor.capabilities
    assert isinstance(caps, ExtractorCapabilities)
    assert caps.chapter_level is True
    assert caps.page_level is False
    diag = extractor.diagnostics()
    assert "ebooklib" in diag


def test_extractor_probe(tmp_path: Path, extractor: EpubExtractor) -> None:
    assert extractor.probe(tmp_path / "book.epub")
    assert not extractor.probe(tmp_path / "book.pdf")
    assert not extractor.probe(tmp_path / "book.txt")
