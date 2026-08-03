"""OpenAI-compatible LLM adapter for real model-driven analysis (PRD P1).

When configured with a ``base_url`` / ``api_key`` / ``model``, this adapter
sends text blocks to an OpenAI-compatible chat endpoint and parses the
structured JSON response into the same shape
:class:`~book2skill.llm.mock_adapter.MockLLMAdapter` produces.

The ``openai`` Python package is an **optional** dependency. A configured
real provider fails closed when the dependency, credentials, network, or
response contract is unavailable. Explicit fallback policy and audit records
belong to :class:`book2skill.llm.runtime.RuntimeLLMAdapter`.

Design notes
------------

- Each method sends a system prompt describing the expected JSON schema,
  plus the text blocks as user content. The model is asked to return a
  JSON array; the response is parsed with :func:`json.loads`.
- Invalid responses raise a typed runtime error rather than silently changing
  the analysis provider.
- The adapter is stateless between calls; no conversation history is kept.
- API keys come only from the constructor or ``OPENAI_API_KEY`` env var
  (never logged).
"""

from __future__ import annotations

import json
import os
from typing import Any

from book2skill.domain import TextBlock
from book2skill.llm.chunking import ChunkItem
from book2skill.llm.runtime import (
    LLMResponseError,
    LLMRuntimeError,
    LLMUnavailableError,
)


class OpenAIAdapter:
    """LLM adapter backed by an OpenAI-compatible chat endpoint.

    Args:
        model: Model name (e.g. ``"gpt-4o"`` or a local model id).
        api_key: API key. Falls back to ``OPENAI_API_KEY`` env var.
        base_url: Optional base URL for OpenAI-compatible endpoints
            (e.g. ``"http://localhost:11434/v1"`` for Ollama).
        temperature: Sampling temperature recorded by the runtime manifest.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.2,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url
        self._temperature = temperature
        self._request_timeout_seconds = request_timeout_seconds
        self._client = self._build_client()

    def _build_client(self) -> Any:
        """Create the OpenAI client, or ``None`` if unavailable.

        Returns ``None`` when the optional client cannot be initialized. The
        public operations then raise :class:`LLMUnavailableError`.
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
        """Detect document structure via the configured real provider."""
        self._require_available()

        prompt = _build_structure_prompt(source_id, blocks)
        raw = self._chat(prompt)
        parsed = _parse_required_json_array(raw)
        normalized = [_normalize_structure(e) for e in parsed]
        return normalized

    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        """Analyze one bounded chunk with a combined response contract."""
        self._require_available()
        raw = self._chat(_build_chunk_prompt(source_id, items))
        payload = _parse_required_json_object(raw)
        structure = payload.get("structure", [])
        candidates = payload.get("candidates", [])
        if not isinstance(structure, list) or not isinstance(candidates, list):
            raise LLMResponseError("LLM chunk response arrays are invalid")
        if any(not isinstance(item, dict) for item in structure + candidates):
            raise LLMResponseError("LLM chunk response items must be objects")
        return {
            "structure": [_normalize_structure(dict(item)) for item in structure],
            "candidates": [_normalize_candidate(dict(item)) for item in candidates],
        }

    def extract_candidates(
        self,
        source_id: str,
        blocks: list[TextBlock],
    ) -> list[dict[str, object]]:
        """Extract candidate knowledge units via the configured provider."""
        self._require_available()

        prompt = _build_candidates_prompt(source_id, blocks)
        raw = self._chat(prompt)
        parsed = _parse_required_json_array(raw)
        normalized = [_normalize_candidate(e) for e in parsed]
        return normalized

    def suggest_skills(
        self,
        source_id: str,
        candidates: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Propose skill shapes via the configured real provider."""
        self._require_available()

        prompt = _build_skills_prompt(source_id, candidates)
        raw = self._chat(prompt)
        parsed = _parse_required_json_array(raw)
        normalized = [_normalize_skill(e) for e in parsed]
        return normalized

    # -- internals --------------------------------------------------------

    def _require_available(self) -> None:
        if not self.is_available:
            raise LLMUnavailableError(
                "The configured OpenAI-compatible provider is unavailable."
            )

    def _chat(self, prompt: str) -> str:
        """Send a chat completion request, raising on a failed request."""
        if self._client is None:
            raise LLMUnavailableError(
                "The configured OpenAI-compatible provider is unavailable."
            )
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
                temperature=self._temperature,
                timeout=getattr(self, "_request_timeout_seconds", 30.0),
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            raise LLMRuntimeError("OpenAI-compatible request failed") from exc


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


def _build_chunk_prompt(source_id: str, items: list[ChunkItem]) -> str:
    """Build one bounded structure-and-candidate request with neighbour context."""
    records = []
    for item in items:
        records.append(
            {
                "input_id": item.input_id,
                "locator": item.locator.model_dump(mode="json"),
                "context_before": item.context_before,
                "text": item.text,
                "context_after": item.context_after,
            }
        )
    return (
        f"Analyse bounded source chunk from '{source_id}'. Return ONLY one JSON "
        "object with arrays 'structure' and 'candidates'. Every item MUST carry "
        "input_id copied exactly from the input. Structure items need heading, "
        "level (1-6), text_preview; candidate items need kind, content, confidence "
        "(0-1), review_status='candidate'. Context is for interpretation only; do "
        "not cite it as an item.\n\n"
        + json.dumps(records, ensure_ascii=False, separators=(",", ":"))
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


def _parse_required_json_array(raw: str) -> list[dict[str, object]]:
    """Parse an array response and reject malformed/non-array payloads."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.split("\n") if not line.strip().startswith("```")
        )
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMResponseError("LLM response was not valid JSON") from exc
    if not isinstance(parsed, list):
        raise LLMResponseError("LLM response must be a JSON array")
    if any(not isinstance(item, dict) for item in parsed):
        raise LLMResponseError("LLM response array items must be objects")
    return [dict(item) for item in parsed]


def _parse_required_json_object(raw: str) -> dict[str, object]:
    """Parse an object response and reject malformed payloads."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.split("\n") if not line.strip().startswith("```")
        )
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMResponseError("LLM response was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise LLMResponseError("LLM chunk response must be a JSON object")
    return dict(parsed)


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
