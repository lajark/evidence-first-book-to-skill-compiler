"""Tests for selectively ported sanitize module."""

from __future__ import annotations

from book2skill.extractors._vendor.book_to_skill.sanitize import sanitize_extracted_text

INVISIBLE_CODEPOINTS = (
    "​"
    "‌"
    "‍"
    "﻿"
    "\U000e0000"
    "\U000e0069"
    "\U000e007f"
)


def test_sanitizer_removes_zero_width_and_tag_block_codepoints() -> None:
    sanitized, removed = sanitize_extracted_text(
        f"before{INVISIBLE_CODEPOINTS}after"
    )
    assert sanitized == "beforeafter"
    assert removed == len(INVISIBLE_CODEPOINTS)


def test_sanitizer_preserves_normal_multilingual_text() -> None:
    original = "Café - 中文 -  - plain ASCII\n"
    sanitized, removed = sanitize_extracted_text(original)
    assert sanitized == original
    assert removed == 0
