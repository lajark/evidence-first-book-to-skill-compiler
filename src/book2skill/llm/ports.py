"""LLM adapter port interfaces.

The pipeline talks to LLMs only through these abstractions, so it can run
offline with a rule-based mock (M1) or against an OpenAI-compatible endpoint
(M3) without changes to the application layer.
"""

from __future__ import annotations

from typing import Protocol

from book2skill.domain import TextBlock


class LLMAdapter(Protocol):
    """Abstract LLM interface for source analysis.

    Implementations analyse a list of :class:`~book2skill.domain.TextBlock`
    records (extracted from a raw source) and produce the structured
    components of an :class:`~book2skill.application.models.AnalysisBundle`.
    """

    def analyze_structure(
        self,
        source_id: str,
        blocks: list[TextBlock],
    ) -> list[dict[str, object]]:
        """Detect document structure (headings, sections, hierarchy).

        Returns a list of structure entries, each a JSON-serialisable dict.
        """
        ...

    def extract_candidates(
        self,
        source_id: str,
        blocks: list[TextBlock],
    ) -> list[dict[str, object]]:
        """Extract candidate knowledge units from text blocks.

        Returns a list of candidate-unit dicts.
        """
        ...

    def suggest_skills(
        self,
        source_id: str,
        candidates: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Propose skill shapes (name, description, rationale)."""
        ...
