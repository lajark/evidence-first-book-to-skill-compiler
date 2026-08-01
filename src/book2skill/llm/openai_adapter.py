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
        """Create the OpenAI client, or ``None`` if unavailable.

        Returns ``None`` when the ``openai`` package is missing OR when no
        API key is configured (the OpenAI client constructor raises
        ``OpenAIError`` on missing credentials). In both cases the adapter
        falls back to the mock adapter.
        """
        if not self._api_key:
            return None
        try:
            from openai import OpenAI
        except ImportError:
            return None
        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        try:
            return OpenAI(**kwargs)
        except Exception:
            return None

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
        normalized = [_normalize_structure(e) for e in parsed]
        return normalized or self._mock.analyze_structure(source_id, blocks)

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
        normalized = [_normalize_candidate(e) for e in parsed]
        return normalized or self._mock.extract_candidates(source_id, blocks)

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
        normalized = [_normalize_skill(e) for e in parsed]
        return normalized or self._mock.suggest_skills(source_id, candidates)

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
        "Identify headings and section hierarchy. Return ONLY a JSON array "
        "where each element has exactly these fields:\n"
        "- block_id: string (e.g. '{source_id}-1')\n"
        "- locator: an object {\"kind\": \"paragraph\"|\"heading\"|\"page\", "
        "\"paragraph\": int|null, \"page\": int|null, \"label\": string|null}\n"
        "- heading: string\n"
        "- level: integer 1-6\n"
        "- text_preview: string\n\n"
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
        "framework. Return ONLY a JSON array where each element has exactly "
        "these fields:\n"
        "- unit_id: string\n"
        "- kind: one of the classes above\n"
        "- content: string\n"
        "- source_refs: array of {source_id, block_id, quote}\n"
        "- confidence: number 0.0-1.0\n"
        "- review_status: one of "
        "'candidate'|'reviewed'|'approved'|'rejected'|'superseded' "
        "(use 'candidate' for new units)\n"
        "- record_version: integer (use 1 for new units)\n\n"
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


# ---------------------------------------------------------------------------
# Output normalisation
# ---------------------------------------------------------------------------
#
# LLMs routinely deviate from the requested schema in small ways (locator as a
# string, ``record_version`` as ``"1.0"``, ``review_status`` as ``"pending"``).
# The prompt asks for exact shapes, but to honour the "graceful degradation,
# never crashes" contract we also coerce the common deviations back into the
# schema. Anything that still cannot be coerced is dropped by the caller's
# per-entry validation; nothing raises out of the adapter.

#: Allowed CandidateUnit.review_status values (mirrors the Pydantic Literal).
_REVIEW_STATUSES = frozenset(
    {"candidate", "reviewed", "approved", "rejected", "superseded"}
)


def _as_int(value: object, default: int) -> int:
    """Coerce *value* to int, falling back to *default*."""
    if isinstance(value, bool):  # bool is an int subclass; guard first
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return default
    return default


def _as_float(value: object, default: float) -> float:
    """Coerce *value* to float, falling back to *default*."""
    if isinstance(value, bool):
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _normalize_structure(entry: dict[str, object]) -> dict[str, object]:
    """Coerce a structure entry to the expected shape in place."""
    loc = entry.get("locator")
    if not isinstance(loc, dict):
        entry["locator"] = {
            "kind": "paragraph",
            "label": loc if isinstance(loc, str) else None,
        }
    entry["level"] = max(1, min(6, _as_int(entry.get("level"), 1)))
    for field in ("block_id", "heading", "text_preview"):
        if not isinstance(entry.get(field), str):
            entry[field] = str(entry.get(field, ""))
    return entry


def _normalize_candidate(entry: dict[str, object]) -> dict[str, object]:
    """Coerce a candidate-unit entry to the expected shape in place."""
    if entry.get("review_status") not in _REVIEW_STATUSES:
        entry["review_status"] = "candidate"
    entry["record_version"] = max(1, _as_int(entry.get("record_version"), 1))
    entry["confidence"] = max(0.0, min(1.0, _as_float(entry.get("confidence"), 0.5)))
    sr = entry.get("source_refs")
    entry["source_refs"] = (
        [x for x in sr if isinstance(x, dict)] if isinstance(sr, list) else []
    )
    for field in ("unit_id", "kind", "content"):
        if not isinstance(entry.get(field), str):
            entry[field] = str(entry.get(field, ""))
    return entry


def _normalize_skill(entry: dict[str, object]) -> dict[str, object]:
    """Coerce a suggested-skill entry to the expected shape in place."""
    for field in ("name", "description", "rationale"):
        if not isinstance(entry.get(field), str):
            entry[field] = str(entry.get(field, ""))
    return entry


__all__ = ["OpenAIAdapter"]
