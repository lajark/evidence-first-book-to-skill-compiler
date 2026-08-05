"""LLM adapter port interfaces.

The pipeline talks to LLMs only through these abstractions, so it can run
offline with a rule-based mock (M1) or against an OpenAI-compatible endpoint
(M3) without changes to the application layer.
"""

from __future__ import annotations

from typing import Literal, Protocol

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


class CandidateSynthesisAdapter(Protocol):
    """Optional capability for bounded evidence-card consolidation.

    It is intentionally separate from :class:`LLMAdapter` so existing SDK
    adapters retain their small legacy contract.  The application only opts
    into the hierarchical path when this capability is present.
    """

    def synthesize_candidates(
        self,
        source_id: str,
        level: Literal["chapter", "book"],
        candidates: list[dict[str, object]],
        structure: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        """Return concise, source-unit-linked synthesized candidates.

        ``structure`` is an optional list of the source's detected section
        headings (StructureEntry-compatible dicts). It is a cheap, deterministic
        organizing anchor the reducer may use to align units to the real book
        structure; it is not source content quoted verbatim.
        """
        ...
