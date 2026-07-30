"""Pydantic models for the Analyze pipeline output.

The :class:`AnalysisBundle` is the single artifact produced by the Analyze
Only mode (PRD FR-03-1). It carries document structure, candidate knowledge
units, a review queue, optional conflicts and suggested skill shapes — but
never a compiled Skill, so a human can review before building.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ConflictRecord and ReviewItem are the canonical domain models re-exported
# here so the AnalysisBundle composes the single source of truth. The domain
# versions are strict supersets (extra optional fields) of the former M1
# definitions; the analysis-bundle schema is permissive for these array items.
from book2skill.domain.knowledge import ConflictRecord, ReviewItem


class StructureEntry(BaseModel):
    """A heading or section detected in the source document."""

    model_config = ConfigDict(extra="forbid")

    block_id: str
    locator: dict[str, Any]
    heading: str
    level: int = Field(..., ge=1, le=6)
    text_preview: str


class CandidateUnit(BaseModel):
    """A provisional knowledge unit awaiting human review."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    kind: str
    content: str = Field(..., min_length=1)
    source_refs: list[dict[str, Any]] = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    review_status: Literal[
        "candidate", "reviewed", "approved", "rejected", "superseded"
    ]
    record_version: int = Field(1, ge=1)


class SuggestedSkill(BaseModel):
    """A proposed Skill shape derived from the analysis."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    rationale: str


class AnalysisBundle(BaseModel):
    """The complete output of the Analyze Only mode.

    Conforms to ``schemas/analysis-bundle.schema.json``. Does NOT contain a
    compiled Skill — that is produced by the Build mode (FR-03-2).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    source_ids: list[str] = Field(..., min_length=1)
    structure: list[StructureEntry]
    candidate_units: list[CandidateUnit]
    review_queue: list[ReviewItem]
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    suggested_skills: list[SuggestedSkill] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Batch orchestration models (TASK-010, PRD FR-02 batch partial success)
# ---------------------------------------------------------------------------

#: Per-file outcome status. ``skipped`` means the gate rejected the input
#: before extraction (missing/empty/oversized/damaged); ``failed`` means the
#: file entered extraction but produced no bundle; ``partial`` means a bundle
#: was produced alongside non-fatal errors; ``success`` means a clean bundle.
FileStatus = Literal["success", "partial", "failed", "skipped"]


class FailureRecord(BaseModel):
    """A serialisable view of a :class:`~book2skill.application.gate.GateError`.

    ``GateError`` is a frozen dataclass holding a :class:`pathlib.Path` and an
    :class:`~book2skill.domain.ErrorCode`, which Pydantic cannot dump to JSON
    directly. This model captures the same fields as plain strings so batch
    results serialise cleanly under ``model_dump(mode="json")``.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    code: str
    message: str
    recovery: str = ""

    @classmethod
    def from_gate_error(cls, err: object) -> FailureRecord:
        """Build a :class:`FailureRecord` from a ``GateError`` instance."""
        return cls(
            path=str(err.path),  # type: ignore[attr-defined]
            code=str(err.code.value),  # type: ignore[attr-defined]
            message=str(err.message),  # type: ignore[attr-defined]
            recovery=str(err.recovery),  # type: ignore[attr-defined]
        )


class FileOutcome(BaseModel):
    """The per-file result of a batch run (one input → one outcome)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    source_id: str | None = None
    status: FileStatus
    format: str | None = None
    bundle: AnalysisBundle | None = None
    errors: list[FailureRecord] = Field(default_factory=list)


class BatchSummary(BaseModel):
    """Aggregate counts over a batch's :class:`FileOutcome` list."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(..., ge=0)
    succeeded: int = Field(..., ge=0)
    partial: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    skipped: int = Field(..., ge=0)


class BatchResult(BaseModel):
    """The complete output of a batch orchestration run.

    Each input file yields one :class:`FileOutcome` with its own bundle (when
    extraction succeeded), so a single corrupt file never blocks the rest.
    ``failure_list`` flattens the errors of every failed/skipped outcome for
    quick consumption by callers that only need the failure manifest.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    outcomes: list[FileOutcome]
    summary: BatchSummary
    failure_list: list[FailureRecord] = Field(default_factory=list)


__all__ = [
    "AnalysisBundle",
    "BatchResult",
    "BatchSummary",
    "CandidateUnit",
    "ConflictRecord",
    "FailureRecord",
    "FileOutcome",
    "FileStatus",
    "ReviewItem",
    "StructureEntry",
    "SuggestedSkill",
]
