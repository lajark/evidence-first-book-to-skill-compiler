"""Tests for selectively ported text reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from book2skill.extractors._vendor.book_to_skill.text import read_text_file


@pytest.fixture()
def sample_path(tmp_path: Path) -> Path:
    return tmp_path / "sample.txt"


def test_read_utf8_file(sample_path: Path) -> None:
    sample_path.write_text("hello world 中文", encoding="utf-8")
    result = read_text_file(str(sample_path))
    assert result == "hello world 中文"


def test_read_utf8_bom_file(sample_path: Path) -> None:
    sample_path.write_bytes("﻿hello world".encode())
    result = read_text_file(str(sample_path))
    assert result == "hello world"


def test_read_utf16_file(sample_path: Path) -> None:
    sample_path.write_bytes("hello world".encode("utf-16"))
    result = read_text_file(str(sample_path))
    assert result is not None
    assert "hello world" in result


def test_read_missing_file() -> None:
    result = read_text_file("/nonexistent/path.txt")
    assert result is None
