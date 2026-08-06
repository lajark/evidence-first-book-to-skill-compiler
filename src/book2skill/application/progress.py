"""Progress reporting protocol for long-running pipeline stages.

The application layer stays UI-neutral: it emits stage updates through a
callable, and the CLI binds a Rich-based renderer to it. Stages are
determinate (``current``/``total``) so a progress bar or a simple line
printer can render them. The callback is optional; when omitted the
pipeline runs silently (used by tests and library callers).

Stage codes (English constants; the CLI maps them to localized labels):

- ``extract``    — text extraction (per file)
- ``structure``  — structure analysis via LLM (per block; slow)
- ``candidates`` — candidate extraction via LLM (per block; slow)
- ``synthesis``  — chapter and book-level knowledge synthesis via LLM
- ``skills``     — skill-shape suggestion via LLM (per source)
- ``compile``    — compile Skill IR/wiki/write the directory (build only)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

#: Per-file text extraction.
STAGE_EXTRACT = "extract"
#: Per-block structure detection (LLM).
STAGE_STRUCTURE = "structure"
#: Per-block candidate extraction (LLM).
STAGE_CANDIDATES = "candidates"
#: Hierarchical consolidation of bounded evidence cards.
STAGE_SYNTHESIS = "synthesis"
#: Per-source skill suggestion (LLM).
STAGE_SKILLS = "skills"
#: Compile tail: persist units, build IR/wiki, write Skill directory.
STAGE_COMPILE = "compile"

ProgressStatus = Literal[
    "started", "running", "completed", "cached", "rate_limited", "retrying", "failed"
]
ProgressMode = Literal["auto", "indeterminate", "estimated", "determinate"]

# These weights describe expected wall-clock contribution, not item counts.
# LLM structure/map and synthesis dominate; extraction and compile are short
# tails. The reporter normalizes the selected pipeline's weights, so the
# Analyze plan (without compile) still reaches exactly 100%.
DEFAULT_STAGE_WEIGHTS: dict[str, float] = {
    STAGE_EXTRACT: 0.05,
    STAGE_STRUCTURE: 0.45,
    STAGE_CANDIDATES: 0.05,
    STAGE_SYNTHESIS: 0.30,
    STAGE_SKILLS: 0.10,
    STAGE_COMPILE: 0.05,
}
ANALYZE_STAGE_ORDER = (
    STAGE_EXTRACT,
    STAGE_STRUCTURE,
    STAGE_CANDIDATES,
    STAGE_SYNTHESIS,
    STAGE_SKILLS,
)
BUILD_STAGE_ORDER = ANALYZE_STAGE_ORDER + (STAGE_COMPILE,)


@dataclass(frozen=True)
class ProgressEvent:
    """A UI-neutral progress update with an optional runtime status.

    ``current`` and ``total`` retain the stable, count-based callback contract.
    ``status`` adds machine-readable context for renderers that opt in, while
    legacy callbacks receive the same four positional values as before.
    """

    stage: str
    current: int
    total: int
    detail: str = ""
    status: ProgressStatus = "running"
    mode: ProgressMode = "auto"
    work_completed: float | None = None
    work_total: float | None = None
    eta_seconds: float | None = None
    eta_lower_seconds: float | None = None
    eta_upper_seconds: float | None = None
    eta_sample_count: int | None = None
    # Weighted whole-pipeline progress. ``overall_total`` is 100.0 when
    # present, keeping the value convenient for both Rich and JSON clients.
    overall_completed: float | None = None
    overall_total: float | None = None


class ProgressReporter(Protocol):
    """Callable invoked before/after each pipeline stage step.

    Args:
        stage: One of the ``STAGE_*`` constants.
        current: 1-based position within the current stage's batch.
        total: Number of steps in the current stage (may change per stage).
        detail: Optional human hint (file name or source id).
    """

    def __call__(
        self, stage: str, current: int, total: int, detail: str = ""
    ) -> None:
        ...


class EventProgressReporter(Protocol):
    """Optional extension implemented by renderers that consume status events."""

    def on_progress_event(self, event: ProgressEvent) -> None:
        ...


class WeightedProgressReporter:
    """Add deterministic whole-pipeline progress to stage events.

    The wrapped reporter may be a modern event consumer or a legacy four
    argument callback. In the latter case the extra overall fields are
    intentionally dropped by :func:`emit_progress`, preserving the public
    compatibility contract.
    """

    def __init__(
        self,
        downstream: ProgressReporter,
        *,
        stages: Sequence[str],
        weights: dict[str, float] | None = None,
    ) -> None:
        unique_stages = tuple(dict.fromkeys(stages))
        if not unique_stages:
            raise ValueError("weighted progress needs at least one stage")
        source_weights = weights or DEFAULT_STAGE_WEIGHTS
        raw = {stage: source_weights.get(stage, 1.0) for stage in unique_stages}
        if any(weight <= 0 for weight in raw.values()):
            raise ValueError("progress stage weights must be positive")
        total = sum(raw.values())
        self._downstream = downstream
        self._stages = unique_stages
        self._weights = {stage: weight / total for stage, weight in raw.items()}
        self._fractions = dict.fromkeys(unique_stages, 0.0)
        self._seen: set[str] = set()

    def __call__(self, stage: str, current: int, total: int, detail: str = "") -> None:
        self.on_progress_event(ProgressEvent(stage, current, total, detail))

    def on_progress_event(self, event: ProgressEvent) -> None:
        """Enrich and forward one event, preserving monotonicity."""
        if event.stage in self._weights:
            stage_index = self._stages.index(event.stage)
            for prior in self._stages[:stage_index]:
                # A stage absent from this execution path was skipped, not
                # left unfinished. This matters when no synthesis is needed.
                if prior not in self._seen:
                    self._fractions[prior] = 1.0
            self._seen.add(event.stage)
            fraction = _event_fraction(event)
            if event.status == "completed":
                fraction = 1.0
            self._fractions[event.stage] = max(
                self._fractions[event.stage], fraction
            )
        overall = 100.0 * sum(
            self._weights[stage] * self._fractions[stage]
            for stage in self._stages
        )
        enriched = replace(event, overall_completed=overall, overall_total=100.0)
        emit_progress(self._downstream, enriched)


def _event_fraction(event: ProgressEvent) -> float:
    """Return a clamped stage fraction from token or count work units."""
    completed = (
        event.work_completed if event.work_completed is not None else event.current
    )
    total = event.work_total if event.work_total is not None else event.total
    if total <= 0:
        return 0.0
    return min(1.0, max(0.0, completed / total))


def weighted_progress(
    downstream: ProgressReporter,
    *,
    stages: Sequence[str],
    weights: dict[str, float] | None = None,
) -> WeightedProgressReporter:
    """Create a weighted reporter unless *downstream* is already wrapped."""
    if isinstance(downstream, WeightedProgressReporter):
        return downstream
    return WeightedProgressReporter(downstream, stages=stages, weights=weights)


def emit_progress(reporter: ProgressReporter, event: ProgressEvent) -> None:
    """Deliver *event* without breaking existing four-argument callbacks."""
    event_handler = getattr(reporter, "on_progress_event", None)
    if callable(event_handler):
        event_handler(event)
        return
    reporter(event.stage, event.current, event.total, event.detail)


def noop_progress(
    stage: str, current: int, total: int, detail: str = ""
) -> None:
    """Default no-op reporter; the pipeline runs silently when no callback."""
    return None


__all__ = [
    "ProgressReporter",
    "ProgressEvent",
    "ProgressStatus",
    "ProgressMode",
    "EventProgressReporter",
    "WeightedProgressReporter",
    "weighted_progress",
    "DEFAULT_STAGE_WEIGHTS",
    "ANALYZE_STAGE_ORDER",
    "BUILD_STAGE_ORDER",
    "emit_progress",
    "noop_progress",
    "STAGE_EXTRACT",
    "STAGE_STRUCTURE",
    "STAGE_CANDIDATES",
    "STAGE_SYNTHESIS",
    "STAGE_SKILLS",
    "STAGE_COMPILE",
]
