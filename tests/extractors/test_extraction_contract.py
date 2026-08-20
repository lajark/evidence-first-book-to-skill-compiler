"""Semantic contract tests for extractor output."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from book2skill.domain import SourceFormat
from book2skill.extractors.base import (
    ExtractionContractError,
    ExtractionResult,
    validate_extraction_result,
)
from book2skill.extractors.text_extractor import TextExtractor


def _source(tmp_path: Path) -> tuple[Path, str, str]:
    path = tmp_path / "book.txt"
    path.write_text("A paragraph with enough content.", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, "source-123", digest


def test_valid_result_passes_contract(tmp_path: Path) -> None:
    path, source_id, digest = _source(tmp_path)
    result = TextExtractor().extract_result(
        path,
        source_id=source_id,
        source_format=SourceFormat.TXT,
        content_sha256=digest,
    )

    validate_extraction_result(
        result,
        source_id=source_id,
        source_format=SourceFormat.TXT,
        content_sha256=digest,
    )


def test_empty_extraction_is_rejected(tmp_path: Path) -> None:
    path, source_id, digest = _source(tmp_path)
    extractor = TextExtractor()
    manifest = extractor.extract_result(
        path,
        source_id=source_id,
        source_format=SourceFormat.TXT,
        content_sha256=digest,
    ).manifest
    result = ExtractionResult(manifest=manifest, blocks=(), entries=())

    with pytest.raises(ExtractionContractError, match="no text blocks"):
        validate_extraction_result(
            result,
            source_id=source_id,
            source_format=SourceFormat.TXT,
            content_sha256=digest,
        )


def test_map_hash_locator_and_id_must_match_block(tmp_path: Path) -> None:
    path, source_id, digest = _source(tmp_path)
    result = TextExtractor().extract_result(
        path,
        source_id=source_id,
        source_format=SourceFormat.TXT,
        content_sha256=digest,
    )
    bad_entry = result.entries[0].model_copy(
        update={
            "block_id": "model-invented-id",
            "text_sha256": "0" * 64,
            "locator": result.entries[0].locator.model_copy(
                update={"paragraph": 2}
            ),
        }
    )
    bad_result = ExtractionResult(
        manifest=result.manifest,
        blocks=result.blocks,
        entries=(bad_entry,),
    )

    with pytest.raises(ExtractionContractError) as exc_info:
        validate_extraction_result(
            bad_result,
            source_id=source_id,
            source_format=SourceFormat.TXT,
            content_sha256=digest,
        )
    message = str(exc_info.value)
    assert "text_sha256" in message
    assert "locator" in message
    assert "deterministic" in message


def test_manifest_must_match_trusted_source(tmp_path: Path) -> None:
    path, source_id, digest = _source(tmp_path)
    result = TextExtractor().extract_result(
        path,
        source_id=source_id,
        source_format=SourceFormat.TXT,
        content_sha256=digest,
    )
    bad_manifest = result.manifest.model_copy(
        update={"source_id": "other-source", "content_sha256": "f" * 64}
    )
    bad_result = ExtractionResult(
        manifest=bad_manifest,
        blocks=result.blocks,
        entries=result.entries,
    )

    with pytest.raises(ExtractionContractError, match="manifest.source_id"):
        validate_extraction_result(
            bad_result,
            source_id=source_id,
            source_format=SourceFormat.TXT,
            content_sha256=digest,
        )
