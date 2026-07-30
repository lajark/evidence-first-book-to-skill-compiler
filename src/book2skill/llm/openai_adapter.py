"""OpenAI-compatible LLM adapter for real model-driven analysis (PRD P1).

When configured with a ``base_url`` / ``api_key`` / ``model``, this adapter
sends text blocks to an OpenAI-compatible chat endpoint and parses the
structured JSON response into the same shape
:class:`~book2skill.llm.mock_adapter.MockLLMAdapter` produces.

The ``openai`` Python package is an **optional** dependency. When it is
missing, or when no API key is configured, the adapter falls back to the
:class:`MockLLMAdapter` so the pipeline still runs offline. This mirrors
the Calibre / Tesseract optional-dependency pattern.

Design notes
------------

- Each method sends a system prompt describing the expected JSON schema,
  plus the text blocks as user content. The model is asked to return a
  JSON array; the response is parsed with :func:`json.loads`.
- When the LLM response cannot be parsed, the adapter falls back to the
  mock adapter for that call (graceful degradation, never crashes).
- The adapter is stateless between calls; no conversation history is kept.
- API keys come only from the constructor or ``OPENAI_API_KEY`` env var
  (never logged).
"""

from __future__ import annotations

import json
import os
from typing import Any

from book2skill.domain import TextBlock
from book2skill.llm.mock_adapter import MockLLMAdapter


class OpenAIAdapter:
    """LLM adapter backed by an OpenAI-compatible chat endpoint.

    Args:
        model: Model name (e.g. ``"gpt-4o"`` or a local model id).
        api_key: API key. Falls back to ``OPENAI_API_KEY`` env var.
        base_url: Optional base URL for OpenAI-compatible endpoints
            (e.g. ``"http://localhost:11434/v1"`` for Ollama).
        mock: Fallback adapter used when the LLM is unavailable or returns
            unparseable output. Defaults to a fresh :class:`MockLLMAdapter`.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        mock: MockLLMAdapter | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url
        self._mock = mock or MockLLMAdapter()
        self._client = self._build_client()

    def _build_client(self) -> Any:
        """Create the OpenAI client, or ``None`` if the package is missing."""
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError:
            return None
        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return OpenAI(**kwargs)

    @property
    def is_available(self) -> bool:
        """``True`` when the client is built and an API key is configured."""
        return self._client is not None and bool(self._api_key)

    # -- LLMAdapter protocol ----------------------------------------------

    def analyze_structure(
        self,
        source_id: str,
        blocks: list[TextBlock],
    ) -> list[dict[str, object]]:
        """Detect document structure via LLM, falling back to mock."""
        if not self.is_available:
            return self._mock.analyze_structure(source_id, blocks)

        prompt = _build_structure_prompt(source_id, blocks)
        raw = self._chat(prompt)
        if raw is None:
            return self._mock.analyze_structure(source_id, blocks)
        parsed = _parse_json_array(raw)
        return parsed or self._mock.analyze_structure(source_id, blocks)

    def extract_candidates(
        self,
        source_id: str,
        blocks: list[TextBlock],
    ) -> list[dict[str, object]]:
        """Extract candidate knowledge units via LLM, falling back to mock."""
        if not self.is_available:
            return self._mock.extract_candidates(source_id, blocks)

        prompt = _build_candidates_prompt(source_id, blocks)
        raw = self._chat(prompt)
        if raw is None:
            return self._mock.extract_candidates(source_id, blocks)
        parsed = _parse_json_array(raw)
        return parsed or self._mock.extract_candidates(source_id, blocks)

    def suggest_skills(
        self,
        source_id: str,
        candidates: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Propose skill shapes via LLM, falling back to mock."""
        if not self.is_available:
            return self._mock.suggest_skills(source_id, candidates)

        prompt = _build_skills_prompt(source_id, candidates)
        raw = self._chat(prompt)
        if raw is None:
            return self._mock.suggest_skills(source_id, candidates)
        parsed = _parse_json_array(raw)
        return parsed or self._mock.suggest_skills(source_id, candidates)

    # -- internals --------------------------------------------------------

    def _chat(self, prompt: str) -> str | None:
        """Send a chat completion request, returning the text or ``None``."""
        if self._client is None:
            return None
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a knowledge-extraction assistant. "
                            "Return ONLY a JSON array, no prose."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )
            return resp.choices[0].message.content or ""
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _build_structure_prompt(
    source_id: str, blocks: list[TextBlock]
) -> str:
    """Build the prompt for structure detection."""
    text = "\n\n".join(
        f"[block {i}] {b.text}" for i, b in enumerate(blocks, 1)
    )
    return (
        f"Analyse the following text blocks from source '{source_id}'. "
        "Identify headings and section hierarchy. Return a JSON array where "
        "each element has: block_id, locator, heading, level (1-6), "
        "text_preview.\n\n"
        f"{text}"
    )


def _build_candidates_prompt(
    source_id: str, blocks: list[TextBlock]
) -> str:
    """Build the prompt for candidate extraction."""
    text = "\n\n".join(
        f"[block {i}] {b.text}" for i, b in enumerate(blocks, 1)
    )
    return (
        f"Extract knowledge units from the following text blocks "
        f"(source '{source_id}'). Classify each as one of: principle, "
        "technique, term, case, anti_pattern, checklist, decision_rule, "
        "framework. Return a JSON array where each element has: unit_id, "
        "kind, content, source_refs (array of {source_id, block_id, quote}), "
        "confidence (0.0-1.0), review_status, record_version.\n\n"
        f"{text}"
    )


def _build_skills_prompt(
    source_id: str, candidates: list[dict[str, object]]
) -> str:
    """Build the prompt for skill suggestions."""
    cands_json = json.dumps(candidates, ensure_ascii=False, indent=2)
    return (
        f"Based on these candidate knowledge units from source "
        f"'{source_id}', propose one or more Agent Skill shapes. "
        "Return a JSON array where each element has: name (lowercase-kebab), "
        "description, rationale.\n\n"
        f"{cands_json}"
    )


def _parse_json_array(raw: str) -> list[dict[str, object]]:
    """Parse a JSON array from a raw LLM string, tolerating code fences."""
    cleaned = raw.strip()
    # Strip markdown code fences if present.
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json or ```) and last line (```).
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


__all__ = ["OpenAIAdapter"]
