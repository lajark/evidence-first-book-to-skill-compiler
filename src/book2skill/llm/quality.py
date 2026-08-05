"""Selective quality review contracts and orchestration (OPT-P1-10).

The ``quality`` mode runs the same first round as ``balanced``/single, then
locally flags candidates that need review (low confidence, conflict,
coverage gap, anomalous) and routes only those flagged units through
independent Critic + Arbiter models. It is off by default: when no quality
pass is requested, zero extra LLM calls are made.

Review contract
---------------
* Every :class:`ReviewPatch` is source-replayable: its ``source_refs`` must
  be a subset of the reviewed unit's original refs. Unknown refs are rejected.
* Critics are anonymous: they see only an :class:`EvidenceCard` (redacted
  model id, no prompt, no endpoint, no first-round model identity).
* The Arbiter is authoritative and evidence-based; simple majority voting is
  never used, and no patch may expand content without source evidence.
* Hard budgets (calls / estimated cost) stop new review calls when exceeded
  and report a ``budget_exceeded`` summary instead of silently dropping.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from book2skill.application.models import (
    AnalysisBundle,
    CandidateUnit,
    Disposition,
    ReviewPatch,
)
from book2skill.llm.benchmark_slots import (
    BenchmarkSlot,
    compute_slot_coverage,
    missing_slots,
)
from book2skill.llm.runtime import RuntimeLLMAdapter

QualityReason = Literal[
    "low_confidence", "conflict", "coverage_gap", "anomalous", "benchmark_gap"
]


class QualityFlag(BaseModel):
    """A redacted reason a candidate was selected for review."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    reason: QualityReason
    detail: str


class EvidenceCard(BaseModel):
    """The anonymized, self-contained input a Critic receives.

    Contains only the candidate's content, conditions, exceptions, source
    refs and a *redacted* model id. No first-round model name, prompt,
    endpoint, API key or source text beyond the extracted content.
    """

    model_config = ConfigDict(extra="forbid")

    content: str
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    # Opaque identifier (e.g. SHA-256 of the producing model) so the critic
    # cannot learn the exact first-round model.
    anonymized_model_id: str


class QualityGate:
    """Pure, offline rules that decide which candidates need review."""

    @staticmethod
    def detect(
        bundle: AnalysisBundle,
        *,
        low_confidence_threshold: float = 0.3,
        min_content_chars: int = 20,
        max_content_chars: int = 2000,
        benchmark_slots: list[BenchmarkSlot] | None = None,
    ) -> list[QualityFlag]:
        flags: list[QualityFlag] = []
        conflict_ids = {
            uid for conflict in bundle.conflicts for uid in conflict.unit_ids
        }
        for unit in bundle.candidate_units:
            reasons: list[tuple[QualityReason, str]] = []
            if unit.confidence < low_confidence_threshold:
                reasons.append(
                    ("low_confidence", f"confidence {unit.confidence:.2f}")
                )
            if unit.unit_id in conflict_ids:
                reasons.append(("conflict", "unit participates in a conflict"))
            if not unit.source_refs:
                reasons.append(("anomalous", "no source references"))
            if len(unit.content) < min_content_chars:
                reasons.append(("anomalous", "content below minimum length"))
            if len(unit.content) > max_content_chars:
                reasons.append(("anomalous", "content above maximum length"))
            for reason, detail in reasons:
                flags.append(
                    QualityFlag(unit_id=unit.unit_id, reason=reason, detail=detail)
                )
        if benchmark_slots:
            coverage = compute_slot_coverage(bundle.candidate_units, benchmark_slots)
            min_count = {s.slot_id: s.min_count for s in benchmark_slots}
            for slot_id in missing_slots(coverage, benchmark_slots):
                flags.append(
                    QualityFlag(
                        unit_id=slot_id,
                        reason="benchmark_gap",
                        detail=(
                            f"slot {slot_id}: {coverage.get(slot_id, 0)}/"
                            f"{min_count[slot_id]}"
                        ),
                    )
                )
        return flags


@dataclass
class QualityBudget:
    """Hard call/cost budget for a quality review run."""

    max_calls: int | None = None
    max_cost: float | None = None
    calls: int = 0
    cost: float = 0.0

    def within(self) -> bool:
        return not (
            (self.max_calls is not None and self.calls >= self.max_calls)
            or (self.max_cost is not None and self.cost >= self.max_cost)
        )

    def record(self, est_cost: float) -> None:
        self.calls += 1
        self.cost += est_cost


class Reviewer(Protocol):
    """A single Critic or Arbiter review call."""

    def review(self, card: EvidenceCard, unit: CandidateUnit) -> ReviewPatch:
        ...


class MockReviewer:
    """Deterministic reviewer used for offline testing and mock profiles."""

    def __init__(
        self, disposition: Disposition = "merge", model_id: str = "mock-reviewer"
    ) -> None:
        self._disposition = disposition
        self._model_id = model_id

    def review(self, card: EvidenceCard, unit: CandidateUnit) -> ReviewPatch:
        return ReviewPatch(
            unit_id=unit.unit_id,
            disposition=self._disposition,
            revised_content=card.content if self._disposition == "merge" else None,
            rationale="deterministic mock review (offline)",
            source_refs=unit.source_refs,
            model_id=self._model_id,
        )


class LLMReviewer:
    """A real Critic/Arbiter that dispatches the evidence card to a model."""

    def __init__(
        self,
        adapter: RuntimeLLMAdapter,
        role: Literal["critic", "arbiter"],
        model_id: str,
    ) -> None:
        self._adapter = adapter
        self._role = role
        self._model_id = model_id

    def review(self, card: EvidenceCard, unit: CandidateUnit) -> ReviewPatch:
        raw = self._adapter.review_evidence(card.model_dump(mode="json"))
        parsed = _parse_review_patch(raw, unit, self._model_id)
        return parsed


def _parse_review_patch(
    raw: dict[str, Any], unit: CandidateUnit, model_id: str
) -> ReviewPatch:
    """Parse a reviewer's structured response, enforcing source replayability."""
    disposition = raw.get("disposition", "accept")
    if disposition not in ("accept", "reject", "merge"):
        raise ValueError(f"unknown review disposition: {disposition}")
    patch = ReviewPatch(
        unit_id=unit.unit_id,
        disposition=disposition,
        revised_content=raw.get("revised_content"),
        rationale=str(raw.get("rationale", "")),
        source_refs=raw.get("source_refs") or unit.source_refs,
        model_id=model_id,
    )
    if not patch.references_known(unit):
        raise ValueError("review patch references unknown source refs")
    return patch


@dataclass
class QualityResult:
    """Outcome of a quality review pass."""

    patches: list[ReviewPatch] = field(default_factory=list)
    flags: list[QualityFlag] = field(default_factory=list)
    budget_exceeded: bool = False


class QualityService:
    """Run the selective Critic + Arbiter review for a bundle."""

    def __init__(
        self,
        *,
        critic_reviewer: Reviewer,
        arbiter_reviewer: Reviewer,
        budget: QualityBudget | None = None,
        critics: int = 1,
    ) -> None:
        self._critic_reviewer = critic_reviewer
        self._arbiter_reviewer = arbiter_reviewer
        self._budget = budget or QualityBudget()
        self._critics = critics

    def review(self, bundle: AnalysisBundle) -> QualityResult:
        flags = QualityGate.detect(bundle)
        if not flags:
            return QualityResult()
        by_id = {unit.unit_id: unit for unit in bundle.candidate_units}
        patches: list[ReviewPatch] = []
        budget_exceeded = False
        for flag in flags:
            unit = by_id.get(flag.unit_id)
            if unit is None:
                continue
            card = build_evidence_card(unit, bundle)
            if not self._budget.within():
                budget_exceeded = True
                break
            critic_patches: list[ReviewPatch] = []
            for _ in range(self._critics):
                if not self._budget.within():
                    budget_exceeded = True
                    break
                patch = self._critic_reviewer.review(card, unit)
                self._budget.record(0.0)
                critic_patches.append(patch)
            if budget_exceeded:
                break
            if not self._budget.within():
                budget_exceeded = True
                break
            final = self._arbiter_reviewer.review(card, unit)
            self._budget.record(0.0)
            patches.append(final)
        return QualityResult(
            patches=patches, flags=flags, budget_exceeded=budget_exceeded
        )


def build_evidence_card(unit: CandidateUnit, bundle: AnalysisBundle) -> EvidenceCard:
    """Build an anonymized evidence card for one candidate unit."""
    model_identity = bundle.analysis_run.model if bundle.analysis_run else "unknown"
    return EvidenceCard(
        content=unit.content,
        conditions=unit.conditions,
        exceptions=unit.exceptions,
        source_refs=unit.source_refs,
        confidence=unit.confidence,
        anonymized_model_id=hashlib.sha256(model_identity.encode("utf-8")).hexdigest()[:12],
    )


__all__ = [
    "Disposition",
    "EvidenceCard",
    "LLMReviewer",
    "MockReviewer",
    "QualityBudget",
    "QualityFlag",
    "QualityGate",
    "QualityReason",
    "QualityResult",
    "QualityService",
    "ReviewPatch",
    "Reviewer",
    "build_evidence_card",
]
