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
- ``skills``     — skill-shape suggestion via LLM (per source)
- ``compile``    — compile Skill IR/wiki/write the directory (build only)
"""

from __future__ import annotations

from typing import Protocol

#: Per-file text extraction.
STAGE_EXTRACT = "extract"
#: Per-block structure detection (LLM).
STAGE_STRUCTURE = "structure"
#: Per-block candidate extraction (LLM).
STAGE_CANDIDATES = "candidates"
#: Per-source skill suggestion (LLM).
STAGE_SKILLS = "skills"
#: Compile tail: persist units, build IR/wiki, write Skill directory.
STAGE_COMPILE = "compile"


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


def noop_progress(
    stage: str, current: int, total: int, detail: str = ""
) -> None:
    """Default no-op reporter; the pipeline runs silently when no callback."""
    return None


__all__ = [
    "ProgressReporter",
    "noop_progress",
    "STAGE_EXTRACT",
    "STAGE_STRUCTURE",
    "STAGE_CANDIDATES",
    "STAGE_SKILLS",
    "STAGE_COMPILE",
]
