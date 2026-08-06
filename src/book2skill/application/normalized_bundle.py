"""Deterministic boundary between model analysis and SkillIR compilation.

The persisted snapshot deliberately stores only source identifiers and hashes
of direct quotes.  It is reproducible from the reviewed AnalysisBundle and does
not become a second source of truth beside the Schema layer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from book2skill import __version__
from book2skill.application.candidates import candidate_to_unit
from book2skill.application.models import AnalysisBundle
from book2skill.domain import (
    DEFAULT_TARGET_SPEC,
    ConflictRecord,
    EvidenceLevel,
    KnowledgeUnit,
    ReviewItem,
    TargetSpec,
)
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.storage.file_storage import atomic_write

NORMALIZED_BUNDLE_FILENAME = "normalized-bundle.json"
SKILL_TEMPLATE_VERSION = "skill-writer-v1"


class NormalizedSourceRef(BaseModel):
    """Sanitized source pointer without verbatim source text."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    block_id: str
    quote_sha256: str | None = None
    quote_chars: int = Field(0, ge=0)


class NormalizedUnit(BaseModel):
    """Stable, reviewed unit representation used by the boundary snapshot."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    unit_id: str
    kind: str
    content: str
    conditions: list[str]
    exceptions: list[str]
    source_refs: list[NormalizedSourceRef]
    confidence: float | None = None
    review_status: str
    record_version: int
    supersedes: str | None = None
    evidence_level: EvidenceLevel = EvidenceLevel.PRIMARY
    evidence_note: str | None = None


class NormalizedBundle(BaseModel):
    """Content-addressed snapshot of the deterministic normalization result."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    normalized_bundle_id: str = Field(..., min_length=64, max_length=64)
    collection_id: str
    input_bundle_sha256: str = Field(..., min_length=64, max_length=64)
    generator_version: str
    template_version: str = SKILL_TEMPLATE_VERSION
    target_spec: TargetSpec
    source_ids: list[str]
    units: list[NormalizedUnit]
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    review_queue: list[ReviewItem] = Field(default_factory=list)


class NormalizationResult(BaseModel):
    """In-memory result: sanitized snapshot plus full units for compilation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    bundle: NormalizedBundle
    units: list[KnowledgeUnit]


def normalize_analysis_bundle(bundle: AnalysisBundle) -> NormalizationResult:
    """Normalize a reviewed AnalysisBundle using deterministic Core rules."""
    units = [candidate_to_unit(candidate) for candidate in bundle.candidate_units]
    _validate_source_refs(bundle.collection_id, bundle.source_ids, units)
    input_hash = _sha256_json(bundle.model_dump(mode="json"))
    normalized = _build_normalized_bundle(
        collection_id=bundle.collection_id,
        source_ids=bundle.source_ids,
        units=units,
        input_bundle_sha256=input_hash,
        conflicts=bundle.conflicts,
        review_queue=bundle.review_queue,
    )
    return NormalizationResult(bundle=normalized, units=units)


def normalize_units(
    *,
    collection_id: str,
    source_ids: list[str],
    units: list[KnowledgeUnit],
) -> NormalizationResult:
    """Create the same boundary snapshot from an existing Schema current view."""
    _validate_source_refs(collection_id, source_ids, units)
    input_hash = _sha256_json(
        [unit.model_dump(mode="json") for unit in _sorted_units(units)]
    )
    normalized = _build_normalized_bundle(
        collection_id=collection_id,
        source_ids=source_ids,
        units=units,
        input_bundle_sha256=input_hash,
        conflicts=[],
        review_queue=[],
    )
    return NormalizationResult(bundle=normalized, units=list(_sorted_units(units)))


def write_normalized_bundle(path: Path, bundle: NormalizedBundle) -> Path:
    """Atomically persist a normalized snapshot."""
    target = Path(path)
    if target.is_dir() or target.suffix == "":
        target = target / NORMALIZED_BUNDLE_FILENAME
    atomic_write(target, bundle.model_dump_json(indent=2))
    return target


def load_normalized_bundle(path: Path) -> NormalizedBundle:
    """Load and verify a persisted normalized snapshot."""
    target = Path(path)
    if target.is_dir():
        target = target / NORMALIZED_BUNDLE_FILENAME
    bundle = NormalizedBundle.model_validate_json(target.read_text(encoding="utf-8"))
    if not verify_normalized_bundle(bundle):
        raise ValueError("normalized bundle identity does not match its content")
    return bundle


def verify_normalized_bundle(bundle: NormalizedBundle) -> bool:
    """Return whether the content-addressed identity is intact."""
    return bundle.normalized_bundle_id == _bundle_id(
        collection_id=bundle.collection_id,
        input_bundle_sha256=bundle.input_bundle_sha256,
        generator_version=bundle.generator_version,
        template_version=bundle.template_version,
        target_spec=bundle.target_spec,
        source_ids=bundle.source_ids,
        units=bundle.units,
        conflicts=bundle.conflicts,
        review_queue=bundle.review_queue,
    )


def _build_normalized_bundle(
    *,
    collection_id: str,
    source_ids: list[str],
    units: list[KnowledgeUnit],
    input_bundle_sha256: str,
    conflicts: list[ConflictRecord],
    review_queue: list[ReviewItem],
) -> NormalizedBundle:
    normalized_units = [_normalize_unit(unit) for unit in _sorted_units(units)]
    sorted_source_ids = sorted(set(source_ids))
    sorted_conflicts = sorted(conflicts, key=lambda item: item.conflict_id)
    sorted_review = sorted(review_queue, key=lambda item: item.item_id)
    bundle_id = _bundle_id(
        collection_id=collection_id,
        input_bundle_sha256=input_bundle_sha256,
        generator_version=__version__,
        template_version=SKILL_TEMPLATE_VERSION,
        target_spec=DEFAULT_TARGET_SPEC,
        source_ids=sorted_source_ids,
        units=normalized_units,
        conflicts=sorted_conflicts,
        review_queue=sorted_review,
    )
    return NormalizedBundle(
        normalized_bundle_id=bundle_id,
        collection_id=collection_id,
        input_bundle_sha256=input_bundle_sha256,
        generator_version=__version__,
        template_version=SKILL_TEMPLATE_VERSION,
        target_spec=DEFAULT_TARGET_SPEC,
        source_ids=sorted_source_ids,
        units=normalized_units,
        conflicts=sorted_conflicts,
        review_queue=sorted_review,
    )


def _normalize_unit(unit: KnowledgeUnit) -> NormalizedUnit:
    refs = []
    for ref in sorted(
        unit.source_refs, key=lambda item: (item.source_id, item.block_id)
    ):
        quote = ref.quote or ""
        refs.append(
            NormalizedSourceRef(
                source_id=ref.source_id,
                block_id=ref.block_id,
                quote_sha256=(
                    hashlib.sha256(quote.encode("utf-8")).hexdigest() if quote else None
                ),
                quote_chars=len(quote),
            )
        )
    return NormalizedUnit(
        unit_id=unit.unit_id,
        kind=str(unit.kind),
        content=unit.content,
        conditions=sorted(unit.conditions),
        exceptions=sorted(unit.exceptions),
        source_refs=refs,
        confidence=unit.confidence,
        review_status=str(unit.review_status),
        record_version=unit.record_version,
        supersedes=unit.supersedes,
        evidence_level=unit.evidence_level,
        evidence_note=unit.evidence_note,
    )


def _validate_source_refs(
    collection_id: str, source_ids: list[str], units: list[KnowledgeUnit]
) -> None:
    known = set(source_ids)
    for unit in units:
        for ref in unit.source_refs:
            if ref.source_id not in known or ref.source_id == "unknown":
                raise DomainError(
                    code=ErrorCode.SCHEMA_VALIDATION_FAILED,
                    input_id=collection_id,
                    message=(
                        f"Unit {unit.unit_id} references unknown source "
                        f"{ref.source_id}."
                    ),
                    recovery=(
                        "Repair the reviewed bundle source_refs and normalize again."
                    ),
                )


def _sorted_units(units: list[KnowledgeUnit]) -> list[KnowledgeUnit]:
    return sorted(units, key=lambda item: (item.unit_id, item.record_version))


def _bundle_id(**identity: object) -> str:
    return _sha256_json(identity)


def _sha256_json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    elif isinstance(value, dict):
        value = {
            key: item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for key, item in value.items()
        }
    payload = json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


__all__ = [
    "NORMALIZED_BUNDLE_FILENAME",
    "SKILL_TEMPLATE_VERSION",
    "NormalizationResult",
    "NormalizedBundle",
    "NormalizedSourceRef",
    "NormalizedUnit",
    "load_normalized_bundle",
    "normalize_analysis_bundle",
    "normalize_units",
    "verify_normalized_bundle",
    "write_normalized_bundle",
]
