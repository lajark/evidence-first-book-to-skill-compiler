"""Rule-based mock LLM adapter for offline analysis (M1).

Produces candidate structure, knowledge units and skill suggestions using
deterministic heuristics. Real LLM-driven analysis is deferred to M3; this
adapter lets the Analyze pipeline run end-to-end without a model endpoint.
"""

from __future__ import annotations

import hashlib

from book2skill.domain import TextBlock

# Heuristic markers for guessing knowledge-unit kinds.
_PRINCIPLE_MARKERS = ("should", "must", "always", "never", "principle", "rule")
_TECHNIQUE_MARKERS = ("step", "method", "approach", "technique", "use", "apply")
_TERM_MARKERS = ("definition", "means", "refers to", "is a", "is an")
_CASE_MARKERS = ("example", "for instance", "case", "scenario")

# Blocks shorter than this are treated as too thin for a standalone unit.
_MIN_BLOCK_CHARS = 20


class MockLLMAdapter:
    """Deterministic, offline LLM substitute using keyword heuristics."""

    def analyze_structure(
        self,
        source_id: str,
        blocks: list[TextBlock],
    ) -> list[dict[str, object]]:
        """Detect headings and section hierarchy from text blocks.

        Markdown-style ``#`` headings are detected directly; other short
        non-sentence lines are treated as potential headings.
        """
        structure: list[dict[str, object]] = []
        for idx, block in enumerate(blocks, start=1):
            first_line = block.text.split("\n", 1)[0].strip()
            level = _heading_level(first_line)
            if level == 0 and not _looks_like_heading(first_line):
                continue
            structure.append(
                {
                    "block_id": f"{source_id}-{idx}",
                    "locator": block.locator.model_dump(mode="json"),
                    "heading": first_line.lstrip("# ").strip(),
                    "level": level or 1,
                    "text_preview": first_line[:120],
                }
            )
        return structure

    def extract_candidates(
        self,
        source_id: str,
        blocks: list[TextBlock],
    ) -> list[dict[str, object]]:
        """Produce one candidate knowledge unit per text block."""
        candidates: list[dict[str, object]] = []
        for idx, block in enumerate(blocks, start=1):
            block_id = f"{source_id}-{idx}"
            kind = _guess_kind(block.text)
            confidence = _estimate_confidence(block.text)
            candidates.append(
                {
                    "unit_id": f"cu-{source_id}-{idx}",
                    "kind": kind,
                    "content": block.text,
                    "source_refs": [
                        {
                            "source_id": source_id,
                            "block_id": block_id,
                            "quote": block.text[:200],
                        }
                    ],
                    "confidence": confidence,
                    "review_status": "candidate",
                    "record_version": 1,
                }
            )
        return candidates

    def suggest_skills(
        self,
        source_id: str,
        candidates: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Propose a single skill derived from the source content."""
        if not candidates:
            return []
        kinds = {str(c["kind"]) for c in candidates}
        # Derive a readable slug from the source id hash for determinism.
        slug_seed = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:8]
        name = f"skill-{slug_seed}"
        description = (
            f"A skill compiled from {len(candidates)} candidate unit(s) "
            f"covering kinds: {', '.join(sorted(kinds))}."
        )
        return [
            {
                "name": name,
                "description": description,
                "rationale": "Auto-suggested by the mock adapter; review before build.",
            }
        ]


# ---------------------------------------------------------------------------
# Heuristic helpers
# ---------------------------------------------------------------------------


def _heading_level(line: str) -> int:
    """Return the Markdown heading level (1-6) or 0 if not a heading."""
    if not line.startswith("#"):
        return 0
    stripped = line.lstrip("#")
    level = len(line) - len(stripped)
    return level if 1 <= level <= 6 else 0


def _looks_like_heading(line: str) -> bool:
    """Heuristic: a short line without sentence-ending punctuation."""
    if not line or len(line) > 80:
        return False
    return not line.endswith((".", "!", "?", ",", ";", ":"))


def _guess_kind(text: str) -> str:
    """Guess a knowledge-unit kind from keyword markers."""
    lower = text.lower()
    if any(m in lower for m in _PRINCIPLE_MARKERS):
        return "principle"
    if any(m in lower for m in _CASE_MARKERS):
        return "case"
    if any(m in lower for m in _TERM_MARKERS):
        return "term"
    if any(m in lower for m in _TECHNIQUE_MARKERS):
        return "technique"
    return "technique"


def _estimate_confidence(text: str) -> float:
    """Assign a confidence score based on block length and content."""
    length = len(text)
    if length < _MIN_BLOCK_CHARS:
        return 0.3
    if length < 100:
        return 0.5
    if length < 500:
        return 0.7
    return 0.8


__all__ = ["MockLLMAdapter"]
