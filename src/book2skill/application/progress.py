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

from dataclasses import dataclass
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
    "emit_progress",
    "noop_progress",
    "STAGE_EXTRACT",
    "STAGE_STRUCTURE",
    "STAGE_CANDIDATES",
    "STAGE_SYNTHESIS",
    "STAGE_SKILLS",
    "STAGE_COMPILE",
]
