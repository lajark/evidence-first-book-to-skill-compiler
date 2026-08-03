"""Trust-boundary validation for externally supplied knowledge artifacts."""

from __future__ import annotations

from collections.abc import Iterable
from typing import NoReturn

from book2skill.application.models import AnalysisBundle
from book2skill.domain import (
    DomainError,
    ErrorCode,
    KnowledgeUnit,
    SourceManifest,
)
from book2skill.storage.ports import RawStorage

_RECOVERY = (
    "Pass --data-home pointing to the Raw storage produced by the same analyze "
    "run, or rerun analyze and keep source_ids and block_ids unchanged."
)


def _raise_invalid(
    input_id: str,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> NoReturn:
    raise DomainError(
        code=ErrorCode.BUILD_SOURCE_TRACE_INVALID,
        input_id=input_id,
        message=message,
        recovery=_RECOVERY,
        details=details,
    )


def _require_raw_storage(
    raw_storage: RawStorage | None,
    *,
    input_id: str,
    subject: str,
) -> RawStorage:
    if raw_storage is None:
        _raise_invalid(
            input_id,
            f"{subject} cannot be verified because Raw storage is unavailable.",
            details={"reason": "raw_storage_unavailable"},
        )
    return raw_storage


def _validate_reference(
    *,
    item_label: str,
    unit_id: str,
    ref_index: int,
    source_id: object,
    block_id: object,
    declared_sources: set[str] | None,
    input_id: str,
) -> tuple[str, str, str]:
    if not isinstance(source_id, str) or not source_id:
        _raise_invalid(
            input_id,
            f"{item_label} {unit_id} has an invalid source_id.",
            details={
                "reason": "invalid_source_ref",
                "unit_id": unit_id,
                "ref_index": ref_index,
            },
        )
    if not isinstance(block_id, str) or not block_id:
        _raise_invalid(
            input_id,
            f"{item_label} {unit_id} has an invalid block_id.",
            details={
                "reason": "invalid_source_ref",
                "unit_id": unit_id,
                "ref_index": ref_index,
            },
        )
    if declared_sources is not None and source_id not in declared_sources:
        _raise_invalid(
            input_id,
            f"{item_label} {unit_id} references undeclared "
            f"source_id {source_id}.",
            details={
                "reason": "undeclared_source",
                "unit_id": unit_id,
                "source_id": source_id,
                "block_id": block_id,
            },
        )
    return unit_id, source_id, block_id


def _verify_references_against_raw(
    *,
    source_ids: list[str],
    references: list[tuple[str, str, str]],
    raw_storage: RawStorage,
    source_version: int,
    input_id: str,
    item_label: str,
) -> list[SourceManifest]:
    manifests: list[SourceManifest] = []
    block_ids_by_source: dict[str, set[str]] = {}
    for source_id in source_ids:
        try:
            complete = raw_storage.exists(source_id, source_version)
        except Exception as exc:  # noqa: BLE001 - normalize storage boundary errors
            _raise_invalid(
                input_id,
                f"Could not verify Raw version {source_version} for "
                f"source {source_id}.",
                details={
                    "reason": "raw_lookup_failed",
                    "source_id": source_id,
                    "version": source_version,
                    "storage_error": type(exc).__name__,
                },
            )
        if not complete:
            _raise_invalid(
                input_id,
                f"Complete Raw version {source_version} is missing for "
                f"source {source_id}.",
                details={
                    "reason": "raw_version_missing",
                    "source_id": source_id,
                    "version": source_version,
                },
            )

        try:
            manifest = raw_storage.load_manifest(source_id, source_version)
            extraction_map = raw_storage.load_extraction_map(
                source_id, source_version
            )
        except Exception as exc:  # noqa: BLE001 - normalize storage boundary errors
            _raise_invalid(
                input_id,
                f"Could not load Raw trace data for source {source_id} "
                f"version {source_version}.",
                details={
                    "reason": "raw_trace_load_failed",
                    "source_id": source_id,
                    "version": source_version,
                    "storage_error": type(exc).__name__,
                },
            )

        if manifest.source_id != source_id or manifest.version != source_version:
            _raise_invalid(
                input_id,
                f"Raw manifest identity does not match source {source_id} "
                f"version {source_version}.",
                details={
                    "reason": "manifest_identity_mismatch",
                    "source_id": source_id,
                    "version": source_version,
                },
            )

        trusted_block_ids: set[str] = set()
        for entry in extraction_map:
            if entry.source_id != source_id:
                _raise_invalid(
                    input_id,
                    f"Extraction block {entry.block_id} belongs to source "
                    f"{entry.source_id}, not {source_id}.",
                    details={
                        "reason": "block_source_mismatch",
                        "source_id": source_id,
                        "block_id": entry.block_id,
                        "entry_source_id": entry.source_id,
                    },
                )
            if entry.block_id in trusted_block_ids:
                _raise_invalid(
                    input_id,
                    f"Raw extraction map for source {source_id} contains "
                    f"duplicate block_id {entry.block_id}.",
                    details={
                        "reason": "duplicate_raw_block_id",
                        "source_id": source_id,
                        "block_id": entry.block_id,
                    },
                )
            trusted_block_ids.add(entry.block_id)
        manifests.append(manifest)
        block_ids_by_source[source_id] = trusted_block_ids

    for unit_id, source_id, block_id in references:
        if block_id not in block_ids_by_source[source_id]:
            _raise_invalid(
                input_id,
                f"{item_label} {unit_id} block_id {block_id} was not found in "
                f"the Raw extraction map for source {source_id}.",
                details={
                    "reason": "raw_block_missing",
                    "unit_id": unit_id,
                    "source_id": source_id,
                    "block_id": block_id,
                },
            )

    return manifests


def verify_bundle_against_raw(
    bundle: AnalysisBundle,
    raw_storage: RawStorage | None,
    *,
    source_version: int,
    input_id: str,
) -> list[SourceManifest]:
    """Verify all external bundle references against immutable Raw records.

    Verification is intentionally fail-closed: callers must provide the Raw
    storage created by the matching Analyze run.  The returned manifests are
    the same records used during verification and can be passed directly to
    the compiler without a second, potentially inconsistent lookup.
    """
    storage = _require_raw_storage(
        raw_storage,
        input_id=input_id,
        subject="AnalysisBundle",
    )

    source_ids = bundle.source_ids
    if len(source_ids) != len(set(source_ids)):
        _raise_invalid(
            input_id,
            "AnalysisBundle source_ids must be unique.",
            details={"reason": "duplicate_source_ids"},
        )
    declared_sources = set(source_ids)

    references: list[tuple[str, str, str]] = []
    for candidate in bundle.candidate_units:
        for ref_index, ref in enumerate(candidate.source_refs):
            references.append(
                _validate_reference(
                    item_label="Candidate",
                    unit_id=candidate.unit_id,
                    ref_index=ref_index,
                    source_id=ref.get("source_id"),
                    block_id=ref.get("block_id"),
                    declared_sources=declared_sources,
                    input_id=input_id,
                )
            )

    return _verify_references_against_raw(
        source_ids=source_ids,
        references=references,
        raw_storage=storage,
        source_version=source_version,
        input_id=input_id,
        item_label="Candidate",
    )


def verify_units_against_raw(
    units: Iterable[KnowledgeUnit],
    raw_storage: RawStorage | None,
    source_version: int,
    input_id: str,
) -> list[SourceManifest]:
    """Verify persisted knowledge references against immutable Raw records."""
    storage = _require_raw_storage(
        raw_storage,
        input_id=input_id,
        subject="KnowledgeUnit records",
    )
    source_ids: list[str] = []
    seen_sources: set[str] = set()
    references: list[tuple[str, str, str]] = []
    for unit in units:
        for ref_index, ref in enumerate(unit.source_refs):
            validated = _validate_reference(
                item_label="KnowledgeUnit",
                unit_id=unit.unit_id,
                ref_index=ref_index,
                source_id=ref.source_id,
                block_id=ref.block_id,
                declared_sources=None,
                input_id=input_id,
            )
            references.append(validated)
            source_id = validated[1]
            if source_id not in seen_sources:
                seen_sources.add(source_id)
                source_ids.append(source_id)

    return _verify_references_against_raw(
        source_ids=source_ids,
        references=references,
        raw_storage=storage,
        source_version=source_version,
        input_id=input_id,
        item_label="KnowledgeUnit",
    )


__all__ = ["verify_bundle_against_raw", "verify_units_against_raw"]
