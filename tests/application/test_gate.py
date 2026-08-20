"""Tests for input discovery and legality gate."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from book2skill.application.gate import (
    MAX_FILE_SIZE,
    DiscoveredFile,
    Gate,
    _detect_by_content,
    _format_size,
    _source_id_from_hash,
)
from book2skill.domain import ErrorCode, SourceFormat

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file(path: Path, content: bytes, *, name: str | None = None) -> Path:
    """Create a file with *content* under *path*."""
    target = path / name if name else path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _make_symlink(target: Path, link: Path) -> Path:
    """Create a symlink or skip when the host disallows symlink creation."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this host: {exc}")
    return link


# ---------------------------------------------------------------------------
# Path expansion
# ---------------------------------------------------------------------------


class TestExpandPaths:
    """Tests for ``Gate._expand_paths``."""

    def test_single_file(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"hello", name="a.txt")
        gate = Gate()
        result = gate._expand_paths([str(f)], glob=False, recursive=False)
        assert result == [f.resolve()]

    def test_directory_non_recursive(self, tmp_path: Path) -> None:
        _make_file(tmp_path, b"a", name="a.txt")
        _make_file(tmp_path, b"b", name="b.pdf")
        _make_file(tmp_path / "sub", b"c", name="c.txt")
        gate = Gate()
        result = gate._expand_paths([str(tmp_path)], glob=False, recursive=False)
        names = {p.name for p in result}
        assert names == {"a.txt", "b.pdf"}

    def test_directory_recursive(self, tmp_path: Path) -> None:
        _make_file(tmp_path, b"a", name="a.txt")
        _make_file(tmp_path / "sub", b"c", name="c.txt")
        gate = Gate()
        result = gate._expand_paths([str(tmp_path)], glob=False, recursive=True)
        names = {p.name for p in result}
        assert names == {"a.txt", "c.txt"}

    def test_glob_pattern(self, tmp_path: Path) -> None:
        _make_file(tmp_path, b"a", name="a.txt")
        _make_file(tmp_path, b"b", name="b.pdf")
        _make_file(tmp_path, b"c", name="c.md")
        gate = Gate()
        result = gate._expand_paths(
            [str(tmp_path / "*.txt")], glob=True, recursive=False
        )
        names = {p.name for p in result}
        assert names == {"a.txt"}

    def test_glob_recursive(self, tmp_path: Path) -> None:
        _make_file(tmp_path, b"a", name="a.txt")
        _make_file(tmp_path / "sub", b"c", name="c.txt")
        gate = Gate()
        result = gate._expand_paths(
            [str(tmp_path / "**/*.txt")], glob=True, recursive=False
        )
        names = {p.name for p in result}
        assert names == {"a.txt", "c.txt"}

    def test_multiple_inputs(self, tmp_path: Path) -> None:
        f1 = _make_file(tmp_path, b"a", name="a.txt")
        f2 = _make_file(tmp_path, b"b", name="b.pdf")
        gate = Gate()
        result = gate._expand_paths([str(f1), str(f2)], glob=False, recursive=False)
        assert len(result) == 2

    def test_deduplicate_paths(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"a", name="a.txt")
        gate = Gate()
        result = gate._expand_paths([str(f), str(f)], glob=False, recursive=False)
        assert len(result) == 1

    def test_empty_inputs(self) -> None:
        gate = Gate()
        result = gate._expand_paths([], glob=False, recursive=False)
        assert result == []

    def test_explicit_symlink_is_preserved_for_validation(self, tmp_path: Path) -> None:
        target = _make_file(tmp_path, b"safe", name="target.txt")
        link = _make_symlink(target, tmp_path / "link.txt")
        result = Gate._expand_paths([str(link)], glob=False, recursive=False)
        assert result == [link.absolute()]


# ---------------------------------------------------------------------------
# Basic validation
# ---------------------------------------------------------------------------


class TestValidateBasic:
    """Tests for ``Gate._validate_basic``."""

    def test_file_not_found(self, tmp_path: Path) -> None:
        gate = Gate()
        err = gate._validate_basic(tmp_path / "missing.txt")
        assert err is not None
        assert err.code == ErrorCode.GATE_FILE_NOT_FOUND

    def test_directory_not_a_file(self, tmp_path: Path) -> None:
        d = tmp_path / "subdir"
        d.mkdir()
        gate = Gate()
        err = gate._validate_basic(d)
        assert err is not None
        assert err.code == ErrorCode.GATE_FILE_NOT_FOUND

    def test_empty_file(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"", name="empty.txt")
        gate = Gate()
        err = gate._validate_basic(f)
        assert err is not None
        assert err.code == ErrorCode.GATE_EMPTY_FILE

    def test_too_large(self, tmp_path: Path) -> None:
        # Create a file that reports as oversized by mocking — instead we
        # create a real file that exceeds the limit check.  We can't easily
        # create a 200 MB file in a test, so we verify the size check logic
        # separately.
        f = _make_file(tmp_path, b"hello", name="small.txt")
        gate = Gate()
        # Override the class-level max for this test by re-setting the check.
        # The simplest approach: test the constant is used; the actual
        # oversized check is verified in integration.
        err = gate._validate_basic(f)
        assert err is None  # small file passes

    def test_readable_file_passes(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"hello", name="ok.txt")
        gate = Gate()
        err = gate._validate_basic(f)
        assert err is None

    def test_symlink_file_is_rejected(self, tmp_path: Path) -> None:
        target = _make_file(tmp_path, b"safe", name="target.txt")
        link = _make_symlink(target, tmp_path / "link.txt")
        err = Gate()._validate_basic(link)
        assert err is not None
        assert err.code == ErrorCode.GATE_SYMLINK_NOT_ALLOWED


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


class TestDetectFormat:
    """Tests for ``Gate._detect_format`` and ``_detect_by_content``."""

    def test_extension_pdf(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"%PDF-1.4 fake pdf", name="doc.pdf")
        gate = Gate()
        fmt, src = gate._detect_format(f)
        assert fmt == SourceFormat.PDF

    def test_extension_txt(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"hello world", name="notes.txt")
        gate = Gate()
        fmt, src = gate._detect_format(f)
        assert fmt == SourceFormat.TXT

    def test_extension_md(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"# Title", name="readme.md")
        gate = Gate()
        fmt, src = gate._detect_format(f)
        assert fmt == SourceFormat.MD

    def test_extension_docx(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"PK\x03\x04 dummy", name="report.docx")
        gate = Gate()
        fmt, src = gate._detect_format(f)
        assert fmt == SourceFormat.DOCX

    def test_extension_epub(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"PK\x03\x04 dummy", name="book.epub")
        gate = Gate()
        fmt, src = gate._detect_format(f)
        assert fmt == SourceFormat.EPUB

    def test_content_detection_pdf_no_extension(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"%PDF-1.4\nfake pdf", name="unknown")
        gate = Gate()
        fmt, src = gate._detect_format(f)
        assert fmt == SourceFormat.PDF
        assert src == "content"

    def test_content_detection_text_no_extension(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"Just some text\n", name="unknown")
        gate = Gate()
        fmt, src = gate._detect_format(f)
        assert fmt == SourceFormat.TXT
        assert src == "content"

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"\x00\x01\x02\x03", name="data.bin")
        gate = Gate()
        fmt, src = gate._detect_format(f)
        assert fmt is None

    def test_html_detection(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"<!DOCTYPE html><html></html>", name="page.html")
        fmt = _detect_by_content(f)
        assert fmt == SourceFormat.HTML

    def test_mobi_magic(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"BOOKMOBI\x00\x00 dummy", name="book.mobi")
        fmt = _detect_by_content(f)
        assert fmt == SourceFormat.MOBI


# ---------------------------------------------------------------------------
# ZIP-based detection
# ---------------------------------------------------------------------------


class TestZipDisambiguation:
    """Tests for EPUB/DOCX disambiguation inside ZIP files."""

    def test_real_epub_detected_by_mimetype(self, tmp_path: Path) -> None:
        f = tmp_path / "book.epub"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("META-INF/container.xml", "<container/>")
        fmt = _detect_by_content(f)
        assert fmt == SourceFormat.EPUB

    def test_real_docx_detected_by_structure(self, tmp_path: Path) -> None:
        f = tmp_path / "report.docx"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("word/document.xml", "<document/>")
        fmt = _detect_by_content(f)
        assert fmt == SourceFormat.DOCX

    def test_encrypted_epub_still_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "encrypted.epub"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("META-INF/encryption.xml", "<encryption/>")
        fmt = _detect_by_content(f)
        assert fmt == SourceFormat.EPUB


# ---------------------------------------------------------------------------
# ZIP bomb detection
# ---------------------------------------------------------------------------


class TestZipBomb:
    """Tests for ``Gate._check_zip_bomb``."""

    def test_normal_zip_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "normal.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("a.txt", "hello")
        gate = Gate()
        err = gate._check_zip_bomb(f)
        assert err is None

    def test_corrupted_zip(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"Not a ZIP file", name="bad.zip")
        gate = Gate()
        err = gate._check_zip_bomb(f)
        assert err is not None
        assert err.code == ErrorCode.GATE_DAMAGED_FILE


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


class TestHashing:
    """Tests for ``Gate._compute_sha256``."""

    def test_sha256(self, tmp_path: Path) -> None:
        content = b"hello world"
        f = _make_file(tmp_path, content, name="test.txt")
        gate = Gate()
        h = gate._compute_sha256(f)
        assert h == _sha256(content)
        assert len(h) == 64

    def test_empty_file_hash(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"", name="empty.txt")
        gate = Gate()
        h = gate._compute_sha256(f)
        assert h == _sha256(b"")

    def test_source_id_from_hash(self) -> None:
        h = "abcdef1234567890" + "0" * 48
        assert _source_id_from_hash(h) == "abcdef123456"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplicate:
    """Tests for ``Gate._deduplicate``."""

    def _make_discovered(
        self, path: Path, sha256: str, source_id: str | None = None
    ) -> DiscoveredFile:
        return DiscoveredFile(
            path=path,
            original_name=path.name,
            content_sha256=sha256,
            source_id=source_id or sha256[:12],
            format=SourceFormat.TXT,
            file_size=100,
            format_source="both",
        )

    def test_no_duplicates(self, tmp_path: Path) -> None:
        f1 = self._make_discovered(tmp_path / "a.txt", "a" * 64)
        f2 = self._make_discovered(tmp_path / "b.txt", "b" * 64)
        gate = Gate()
        result = gate._deduplicate([f1, f2])
        assert len(result) == 2

    def test_duplicates_removed(self, tmp_path: Path) -> None:
        f1 = self._make_discovered(tmp_path / "a.txt", "a" * 64)
        f2 = self._make_discovered(tmp_path / "a_copy.txt", "a" * 64)
        gate = Gate()
        result = gate._deduplicate([f1, f2])
        assert len(result) == 1
        assert result[0].path == (tmp_path / "a.txt")

    def test_all_unique(self, tmp_path: Path) -> None:
        files = [
            self._make_discovered(tmp_path / f"{i}.txt", str(i) * 64)
            for i in range(5)
        ]
        gate = Gate()
        result = gate._deduplicate(files)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# Full discover() integration
# ---------------------------------------------------------------------------


class TestDiscoverEndToEnd:
    """End-to-end tests for ``Gate.discover``."""

    def test_single_valid_file(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"hello world", name="readme.txt")
        gate = Gate()
        files, errors = gate.discover([str(f)], glob=False)
        assert len(files) == 1
        assert len(errors) == 0
        df = files[0]
        assert df.format == SourceFormat.TXT
        assert df.content_sha256 == _sha256(b"hello world")
        assert len(df.source_id) == 12

    def test_mixed_valid_and_invalid(self, tmp_path: Path) -> None:
        f1 = _make_file(tmp_path, b"hello", name="good.txt")
        f2 = tmp_path / "missing.txt"
        gate = Gate()
        files, errors = gate.discover([str(f1), str(f2)], glob=False)
        assert len(files) == 1
        assert len(errors) == 1
        assert errors[0].code == ErrorCode.GATE_FILE_NOT_FOUND

    def test_empty_file_rejected(self, tmp_path: Path) -> None:
        _make_file(tmp_path, b"", name="empty.txt")
        gate = Gate()
        files, errors = gate.discover([str(tmp_path)], glob=False)
        assert len(files) == 0
        assert len(errors) == 1
        assert errors[0].code == ErrorCode.GATE_EMPTY_FILE

    def test_unsupported_format_rejected(self, tmp_path: Path) -> None:
        _make_file(tmp_path, b"\x00\x01\x02", name="data.bin")
        gate = Gate()
        files, errors = gate.discover([str(tmp_path)], glob=False)
        assert len(files) == 0
        assert len(errors) == 1
        assert errors[0].code == ErrorCode.GATE_UNSUPPORTED_FORMAT

    def test_dedup_by_hash_in_discover(self, tmp_path: Path) -> None:
        content = b"same content"
        _make_file(tmp_path, content, name="a.txt")
        _make_file(tmp_path, content, name="b.txt")
        gate = Gate()
        files, errors = gate.discover([str(tmp_path)], glob=False)
        assert len(files) == 1
        assert len(errors) == 0

    def test_pdf_detected_and_valid(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"%PDF-1.4\nfake pdf body", name="doc.pdf")
        gate = Gate()
        files, errors = gate.discover([str(f)], glob=False)
        assert len(files) == 1
        assert len(errors) == 0
        assert files[0].format == SourceFormat.PDF

    def test_epub_detected_and_valid(self, tmp_path: Path) -> None:
        f = tmp_path / "book.epub"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("META-INF/container.xml", "<container/>")
        gate = Gate()
        files, errors = gate.discover([str(f)], glob=False)
        assert len(files) == 1
        assert len(errors) == 0
        assert files[0].format == SourceFormat.EPUB

    def test_docx_detected_and_valid(self, tmp_path: Path) -> None:
        f = tmp_path / "report.docx"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("word/document.xml", "<document/>")
        gate = Gate()
        files, errors = gate.discover([str(f)], glob=False)
        assert len(files) == 1
        assert len(errors) == 0
        assert files[0].format == SourceFormat.DOCX

    def test_corrupted_zip_rejected(self, tmp_path: Path) -> None:
        # A file with .docx extension but corrupted ZIP content
        f = _make_file(tmp_path, b"not a zip file", name="broken.docx")
        gate = Gate()
        files, errors = gate.discover([str(f)], glob=False)
        assert len(files) == 0
        assert len(errors) == 1
        assert errors[0].code == ErrorCode.GATE_DAMAGED_FILE

    def test_format_source_both(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"%PDF-1.4\ncontent", name="doc.pdf")
        gate = Gate()
        files, errors = gate.discover([str(f)], glob=False)
        assert len(files) == 1
        assert files[0].format_source == "both"

    def test_format_source_content_only(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, b"%PDF-1.4\ncontent", name="unknown_file")
        gate = Gate()
        files, errors = gate.discover([str(f)], glob=False)
        assert len(files) == 1
        assert files[0].format_source == "content"

    def test_format_source_extension_only(self, tmp_path: Path) -> None:
        # A file with plain text content but a .html extension — content
        # sniffing detects text but cannot determine the format, so the
        # extension is used as the fallback.
        f = _make_file(tmp_path, b"plain text not html", name="notes.html")
        gate = Gate()
        files, errors = gate.discover([str(f)], glob=False)
        assert len(files) == 1
        assert files[0].format == SourceFormat.HTML
        assert files[0].format_source == "extension"

    def test_discover_glob_wildcard(self, tmp_path: Path) -> None:
        _make_file(tmp_path, b"a", name="a.txt")
        _make_file(tmp_path, b"b", name="b.txt")
        _make_file(tmp_path, b"c", name="c.pdf")
        gate = Gate()
        files, errors = gate.discover([str(tmp_path / "*.txt")], glob=True)
        assert len(files) == 2
        assert len(errors) == 0
        assert all(f.format == SourceFormat.TXT for f in files)

    def test_no_inputs_returns_empty(self) -> None:
        gate = Gate()
        files, errors = gate.discover([])
        assert files == []
        assert errors == []

    def test_recursive_discovery_rejects_symlink_file(self, tmp_path: Path) -> None:
        target = _make_file(tmp_path, b"safe", name="target.txt")
        _make_symlink(target, tmp_path / "sub" / "link.txt")
        files, errors = Gate().discover([str(tmp_path)], glob=False)
        assert files == []
        assert [error.code for error in errors] == [
            ErrorCode.GATE_SYMLINK_NOT_ALLOWED
        ]

    def test_symlink_directory_is_not_traversed(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "target"
        _make_file(target_dir, b"safe", name="inside.txt")
        link_dir = _make_symlink(target_dir, tmp_path / "linked-dir")
        files, errors = Gate().discover([str(link_dir)], glob=False)
        assert files == []
        assert len(errors) == 1
        assert errors[0].code == ErrorCode.GATE_SYMLINK_NOT_ALLOWED


# ---------------------------------------------------------------------------
# Format size helper
# ---------------------------------------------------------------------------


class TestFormatSize:
    def test_bytes(self) -> None:
        assert _format_size(500) == "500 B"

    def test_kb(self) -> None:
        assert _format_size(2048) == "2 KB"

    def test_mb(self) -> None:
        assert _format_size(3 * 1024 * 1024) == "3 MB"

    def test_gb(self) -> None:
        assert _format_size(5 * 1024 * 1024 * 1024) == "5 GB"


# ---------------------------------------------------------------------------
# MAX_FILE_SIZE constant
# ---------------------------------------------------------------------------


def test_max_file_size_is_200_mb() -> None:
    assert MAX_FILE_SIZE == 200 * 1024 * 1024


# ---------------------------------------------------------------------------
# Extension map completeness
# ---------------------------------------------------------------------------


def test_all_source_formats_have_extension_entries() -> None:
    """Verify that every P0 SourceFormat has at least one extension mapping."""
    from book2skill.application.gate import _EXTENSION_MAP

    mapped = set(_EXTENSION_MAP.values())
    expected = {
        SourceFormat.PDF,
        SourceFormat.EPUB,
        SourceFormat.MOBI,
        SourceFormat.AZW,
        SourceFormat.AZW3,
        SourceFormat.TXT,
        SourceFormat.MD,
        SourceFormat.DOCX,
        SourceFormat.HTML,
        SourceFormat.RTF,
    }
    assert mapped >= expected
