"""Tests for the MOBI/AZW extractor adapter.

Covers DRM detection, the Calibre-unavailable degradation path, and the
damaged-file path without depending on the host environment (Calibre
availability is faked via monkeypatch). A synthetic round-trip test
generates a minimal MOBI from plain text via ``ebook-convert`` and extracts
it back when Calibre is present — no pre-existing MOBI fixture or
copyrighted content is needed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from book2skill.domain import DomainError, ErrorCode, SourceFormat
from book2skill.extractors.mobi_extractor import MobiExtractor

_HAS_EBOOK_CONVERT = shutil.which("ebook-convert") is not None


@pytest.fixture()
def extractor() -> MobiExtractor:
    return MobiExtractor()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_pdb(enc_type: int = 0) -> bytes:
    """Build a minimal PalmDB/MOBI container with the given encryption type.

    Layout: 78-byte PDB header + 8-byte record-0 index entry + 16-byte
    PalmDOC header (record 0). Only the fields inspected by
    :meth:`MobiExtractor._looks_like_drm` are populated.
    """
    header = bytearray(78)
    header[0:8] = b"drm-test"  # name; rest zero-padded
    header[76:78] = (1).to_bytes(2, "big")  # nrecords = 1
    rec0_offset = 78 + 8  # = 86
    index = bytearray(8)
    index[0:4] = rec0_offset.to_bytes(4, "big")
    rec0 = bytearray(16)  # PalmDOC header
    rec0[12:14] = enc_type.to_bytes(2, "big")  # encryption type
    return bytes(header) + bytes(index) + bytes(rec0)


# ---------------------------------------------------------------------------
# DRM detection
# ---------------------------------------------------------------------------


class TestDrmDetection:
    """DRM is detected via the PalmDOC encryption-type flag."""

    def test_drm_file_is_rejected_before_extraction(
        self, tmp_path: Path, extractor: MobiExtractor
    ) -> None:
        """A non-zero encryption type → GATE_ENCRYPTED_FILE, no Calibre needed."""
        path = tmp_path / "book.mobi"
        path.write_bytes(_build_pdb(enc_type=1))

        with pytest.raises(DomainError) as exc_info:
            extractor.extract_text_blocks(path)

        assert exc_info.value.code == ErrorCode.GATE_ENCRYPTED_FILE
        assert "DRM" in exc_info.value.recovery

    def test_drm_check_passes_for_non_encrypted(
        self, tmp_path: Path, extractor: MobiExtractor
    ) -> None:
        """A zero encryption type is not flagged as DRM."""
        path = tmp_path / "clean.mobi"
        path.write_bytes(_build_pdb(enc_type=0))
        assert extractor._looks_like_drm(path) is False

    def test_drm_check_short_file_returns_false(
        self, tmp_path: Path, extractor: MobiExtractor
    ) -> None:
        """Files too small to hold a PDB header are not flagged as DRM."""
        path = tmp_path / "tiny.mobi"
        path.write_bytes(b"dummy")
        assert extractor._looks_like_drm(path) is False


class TestCalibreMissingDegradation:
    """Calibre unavailable → EXTRACT_UNSUPPORTED with install guidance."""

    def test_missing_calibre_raises_extract_unsupported(
        self, tmp_path: Path, extractor: MobiExtractor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "book2skill.extractors.mobi_extractor.shutil.which",
            lambda *_a, **_k: None,
        )
        path = tmp_path / "book.mobi"
        path.write_bytes(b"dummy")  # too small to look like DRM

        with pytest.raises(DomainError) as exc_info:
            extractor.extract_text_blocks(path)

        assert exc_info.value.code == ErrorCode.EXTRACT_UNSUPPORTED
        assert "calibre" in exc_info.value.recovery.lower()

    def test_missing_calibre_diagnostics_reports_false(
        self, extractor: MobiExtractor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "book2skill.extractors.mobi_extractor.shutil.which",
            lambda *_a, **_k: None,
        )
        assert extractor.diagnostics() == {"ebook-convert": False}


class TestDamagedFile:
    """Calibre runs but yields no text → GATE_DAMAGED_FILE."""

    def test_no_text_output_raises_damaged(
        self, tmp_path: Path, extractor: MobiExtractor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pretend Calibre is installed and returns no text.
        monkeypatch.setattr(
            "book2skill.extractors.mobi_extractor.shutil.which",
            lambda *_a, **_k: "/fake/ebook-convert",
        )
        monkeypatch.setattr(
            "book2skill.extractors.mobi_extractor.extract_with_ebook_convert",
            lambda _p: None,
        )
        path = tmp_path / "book.mobi"
        path.write_bytes(b"dummy")  # non-DRM (too short to flag)

        with pytest.raises(DomainError) as exc_info:
            extractor.extract_text_blocks(path)

        assert exc_info.value.code == ErrorCode.GATE_DAMAGED_FILE


# ---------------------------------------------------------------------------
# Real round-trip (requires Calibre on PATH)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _HAS_EBOOK_CONVERT,
    reason="Calibre ebook-convert CLI not installed",
)
def test_extractor_round_trip_via_synthetic_mobi(
    tmp_path: Path, extractor: MobiExtractor
) -> None:
    """Generate a minimal MOBI from a plain-text file, then extract it back.

    This is a self-contained round-trip: no pre-existing fixture, no
    copyrighted content — just a few sentences of synthetic text. It also
    exercises that a Calibre-generated (DRM-free) MOBI is not falsely
    flagged as DRM.
    """
    txt_path = tmp_path / "input.txt"
    txt_path.write_text(
        "First paragraph of synthetic text.\n\n"
        "Second paragraph, also synthetic.\n\n"
        "Third paragraph for good measure.",
        encoding="utf-8",
    )

    mobi_path = tmp_path / "output.mobi"
    result = subprocess.run(
        ["ebook-convert", str(txt_path), str(mobi_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"ebook-convert failed: {result.stderr}"
    assert mobi_path.exists(), "MOBI not generated"

    # A Calibre-generated MOBI must not be mistaken for DRM.
    assert extractor._looks_like_drm(mobi_path) is False

    manifest, entries = extractor.extract(mobi_path, source_id="a" * 64)

    assert manifest.source_id == "a" * 64
    assert manifest.format == SourceFormat.MOBI
    assert manifest.extractor == "book_to_skill.calibre"
    assert len(entries) >= 1
    assert entries[0].block_id.endswith("-p1")


@pytest.mark.skipif(
    not _HAS_EBOOK_CONVERT,
    reason="Calibre ebook-convert CLI not installed",
)
def test_extractor_produces_paragraph_entries(
    tmp_path: Path, extractor: MobiExtractor
) -> None:
    # This test requires a real MOBI file, which we do not ship to avoid
    # copyright concerns. It is a placeholder that exercises the full path
    # when a fixture is supplied via the B2S_MOBI_FIXTURE environment variable.
    import os

    fixture = os.environ.get("B2S_MOBI_FIXTURE")
    if not fixture or not Path(fixture).exists():
        pytest.skip("set B2S_MOBI_FIXTURE to a real MOBI path to run this test")

    source_path = Path(fixture)
    manifest, entries = extractor.extract(source_path, source_id="a" * 64)

    assert manifest.source_id == "a" * 64
    assert manifest.format == SourceFormat.MOBI
    assert manifest.extractor == "book_to_skill.calibre"
    assert len(entries) >= 1
