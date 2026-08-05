"""Tests for the OpenAI-compatible LLM adapter (PRD P1).

The ``openai`` package is not installed in the test environment, so these
tests mock the client to verify prompt construction, response parsing, and
typed failure handling for the runtime policy layer.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from book2skill.domain import Locator, LocatorKind, TextBlock
from book2skill.llm.chunking import ChunkItem
from book2skill.llm.openai_adapter import (
    OpenAIAdapter,
    _build_chunk_prompt,
    _build_synthesis_prompt,
    _parse_json_array,
    _render_heading_anchors,
)
from book2skill.llm.runtime import LLMResponseError, LLMRuntimeError


def _make_blocks() -> list[TextBlock]:
    return [
        TextBlock(
            text=(
                "The Single Responsibility Principle states that a class "
                "should have one reason to change."
            ),
            locator=Locator(kind=LocatorKind.PARAGRAPH, paragraph=1),
        ),
    ]


class TestAvailability:
    """Fail-closed behaviour when a real provider is unavailable."""

    def test_adapter_with_api_key(self) -> None:
        """With an api_key, availability depends on whether openai is installed."""
        adapter = OpenAIAdapter(api_key="sk-test")
        import importlib.util

        openai_installed = importlib.util.find_spec("openai") is not None
        assert adapter.is_available is openai_installed

    def test_unavailable_without_api_key(self) -> None:
        adapter = OpenAIAdapter()
        assert adapter.is_available is False

    def test_no_crash_when_openai_rejects_missing_credentials(self) -> None:
        """Constructing OpenAIAdapter without a key must not raise.

        The OpenAI client raises OpenAIError on missing credentials; the
        adapter must catch this and set _client=None (fall back to mock).
        """
        adapter = OpenAIAdapter()  # No api_key, no OPENAI_API_KEY env.
        assert adapter._client is None  # noqa: SLF001
        assert adapter.is_available is False

    def test_unavailable_provider_raises(self) -> None:
        adapter = OpenAIAdapter()
        with pytest.raises(LLMRuntimeError):
            adapter.analyze_structure("src1", _make_blocks())


class TestResponseParsing:
    """_parse_json_array tolerates fenced and malformed responses."""

    def test_plain_json_array(self) -> None:
        raw = '[{"a": 1}, {"b": 2}]'
        result = _parse_json_array(raw)
        assert len(result) == 2
        assert result[0] == {"a": 1}

    def test_fenced_json_array(self) -> None:
        raw = '```json\n[{"a": 1}]\n```'
        result = _parse_json_array(raw)
        assert len(result) == 1
        assert result[0] == {"a": 1}

    def test_malformed_returns_empty(self) -> None:
        assert _parse_json_array("not json at all") == []

    def test_non_array_returns_empty(self) -> None:
        assert _parse_json_array('{"not": "array"}') == []

    def test_non_dict_items_filtered(self) -> None:
        raw = '[{"a": 1}, "not a dict", 42]'
        result = _parse_json_array(raw)
        assert len(result) == 1
        assert result[0] == {"a": 1}


def test_chunk_prompt_requires_actionable_paraphrases() -> None:
    prompt = _build_chunk_prompt(
        "src1",
        [
            ChunkItem("input", "block", "Source content", _make_blocks()[0].locator)
        ],
    )

    assert "concise, original-language paraphrase" in prompt
    assert "20 consecutive CJK characters" in prompt
    assert "state changes" in prompt
    assert "at most 8 candidates" in prompt


def test_chunk_prompt_locale_directs_content_language() -> None:
    """Locale must thread into the prompt so content matches the target language."""
    item = [
        ChunkItem("input", "block", "Source content", _make_blocks()[0].locator)
    ]
    zh = _build_chunk_prompt("src1", item, "zh-CN")
    en = _build_chunk_prompt("src1", item, "en")
    assert "Simplified Chinese (zh-CN)" in zh
    assert "must be written in Chinese" in zh
    assert "Simplified Chinese" not in en
    assert "Respond strictly in English" in en


def test_synthesis_prompt_includes_heading_anchors_when_provided() -> None:
    structure = [{"heading": "第一章 始计"}, {"heading": "第二章 作战"}]
    prompt = _build_synthesis_prompt("src1", "book", [], structure)
    assert "Source section headings" in prompt
    assert "1. 第一章 始计" in prompt
    assert "2. 第二章 作战" in prompt
    # Anchors sit before the candidates payload block.
    assert prompt.index("Source section headings") < prompt.index("[")


def test_synthesis_prompt_omits_anchors_when_no_structure() -> None:
    prompt = _build_synthesis_prompt("src1", "book", [])
    assert "Source section headings" not in prompt


def test_synthesis_prompt_ignores_empty_headings() -> None:
    structure = [{"heading": ""}, {"heading": "   "}, {"other": "x"}, {}]
    prompt = _build_synthesis_prompt("src1", "chapter", [], structure)
    assert "Source section headings" not in prompt


def test_render_heading_anchors_deduplicates_and_caps() -> None:
    structure = [{"heading": "A"}] * 5 + [{"heading": f"S{i}"} for i in range(40)]
    rendered = _render_heading_anchors(structure)
    lines = rendered.strip().splitlines()
    # Header line + 32 anchored headings (dedup A, then S0..S30).
    assert len(lines) == 1 + 32
    assert "3. A" not in "\n".join(lines)  # A deduplicated after first occurrence
    assert "S31" not in rendered  # capped at 32


class TestMockedClient:
    """When a client is injected, the adapter calls it and parses results."""

    def _make_adapter_with_mock_client(
        self, response_text: str
    ) -> OpenAIAdapter:
        """Build an OpenAIAdapter with a fake client returning *response_text*."""
        adapter = OpenAIAdapter.__new__(OpenAIAdapter)
        adapter._model = "test-model"
        adapter._api_key = "sk-test"
        adapter._base_url = None
        adapter._temperature = 0.2
        adapter._request_timeout_seconds = 12.5
        adapter._locale = "zh-CN"

        # Build a fake client.
        fake_choice = MagicMock()
        fake_choice.message.content = response_text
        fake_resp = MagicMock()
        fake_resp.choices = [fake_choice]
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_resp
        adapter._client = fake_client
        return adapter

    def test_analyze_structure_with_llm(self) -> None:
        llm_response = json.dumps(
            [
                {
                    "block_id": "src1-1",
                    "locator": {"kind": "paragraph", "paragraph": 1},
                    "heading": "SRP",
                    "level": 1,
                    "text_preview": "Single Responsibility",
                }
            ]
        )
        adapter = self._make_adapter_with_mock_client(llm_response)
        assert adapter.is_available is True

        blocks = _make_blocks()
        result = adapter.analyze_structure("src1", blocks)
        assert len(result) == 1
        assert result[0]["heading"] == "SRP"
        # Verify the client was called.
        adapter._client.chat.completions.create.assert_called_once()
        assert (
            adapter._client.chat.completions.create.call_args.kwargs["timeout"]
            == 12.5
        )

    def test_extract_candidates_with_llm(self) -> None:
        llm_response = json.dumps(
            [
                {
                    "unit_id": "cu-src1-1",
                    "kind": "principle",
                    "content": "SRP text",
                    "source_refs": [
                        {"source_id": "src1", "block_id": "src1-1", "quote": "SRP"}
                    ],
                    "confidence": 0.9,
                    "review_status": "candidate",
                    "record_version": 1,
                }
            ]
        )
        adapter = self._make_adapter_with_mock_client(llm_response)
        blocks = _make_blocks()
        result = adapter.extract_candidates("src1", blocks)
        assert len(result) == 1
        assert result[0]["kind"] == "principle"

    def test_suggest_skills_with_llm(self) -> None:
        llm_response = json.dumps(
            [
                {
                    "name": "solid-principles",
                    "description": "A skill about SOLID principles.",
                    "rationale": "Derived from SRP content.",
                }
            ]
        )
        adapter = self._make_adapter_with_mock_client(llm_response)
        result = adapter.suggest_skills("src1", [{"kind": "principle"}])
        assert len(result) == 1
        assert result[0]["name"] == "solid-principles"

    def test_synthesize_candidates_with_llm(self) -> None:
        adapter = self._make_adapter_with_mock_client(
            json.dumps(
                {
                    "candidates": [
                        {
                            "kind": "framework",
                            "content": "Start a bounded work cycle.",
                            "confidence": "0.9",
                            "source_unit_ids": ["cu-1"],
                            "conditions": ["When work is selected."],
                            "exceptions": ["When interrupted."],
                        }
                    ]
                }
            )
        )

        result = adapter.synthesize_candidates(
            "src1", "chapter", [{"unit_id": "cu-1", "content": "card"}]
        )

        assert result[0]["kind"] == "framework"
        assert result[0]["source_unit_ids"] == ["cu-1"]
        assert result[0]["confidence"] == 0.9

    def test_llm_returns_malformed_response_error(self) -> None:
        adapter = self._make_adapter_with_mock_client("not valid json")
        with pytest.raises(LLMResponseError):
            adapter.extract_candidates("src1", _make_blocks())


class TestOutputNormalization:
    """The adapter coerces common LLM schema deviations so the pipeline
    does not crash on real models (e.g. locator as a string,
    review_status='pending', record_version='1.0').
    """

    def _make_adapter(self, response_text: str) -> OpenAIAdapter:
        adapter = OpenAIAdapter.__new__(OpenAIAdapter)
        adapter._model = "test-model"
        adapter._api_key = "sk-test"
        adapter._base_url = None
        adapter._temperature = 0.2
        adapter._request_timeout_seconds = 12.5
        adapter._locale = "zh-CN"
        fake_choice = MagicMock()
        fake_choice.message.content = response_text
        fake_resp = MagicMock()
        fake_resp.choices = [fake_choice]
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_resp
        adapter._client = fake_client
        return adapter

    def test_structure_coerces_string_locator(self) -> None:
        from book2skill.application.models import StructureEntry

        raw = json.dumps(
            [
                {
                    "block_id": "1",
                    "locator": "line 1",  # string instead of dict
                    "heading": "Intro",
                    "level": "9",  # out-of-range string
                    "text_preview": "preview",
                }
            ]
        )
        adapter = self._make_adapter(raw)
        result = adapter.analyze_structure("src1", _make_blocks())
        assert len(result) == 1
        entry = result[0]
        assert isinstance(entry["locator"], dict)
        assert entry["level"] == 6  # clamped into 1-6
        # The coerced entry must validate against the strict model.
        StructureEntry.model_validate(entry)

    def test_candidate_coerces_review_status_and_record_version(self) -> None:
        from book2skill.application.models import CandidateUnit

        raw = json.dumps(
            [
                {
                    "unit_id": "cu-1",
                    "kind": "principle",
                    "content": "A real principle worth extracting.",
                    "source_refs": [
                        {"source_id": "src1", "block_id": "1", "quote": "x"}
                    ],
                    "confidence": "0.9",  # string
                    "review_status": "pending",  # not in allowed set
                    "record_version": "1.0",  # float-as-string
                }
            ]
        )
        adapter = self._make_adapter(raw)
        result = adapter.extract_candidates("src1", _make_blocks())
        assert len(result) == 1
        entry = result[0]
        assert entry["review_status"] == "candidate"
        assert entry["record_version"] == 1
        assert entry["confidence"] == 0.9
        # The coerced entry must validate against the strict model.
        CandidateUnit.model_validate(entry)

    def test_llm_exception_raises_runtime_error(self) -> None:
        adapter = OpenAIAdapter.__new__(OpenAIAdapter)
        adapter._model = "test-model"
        adapter._api_key = "sk-test"
        adapter._base_url = None
        adapter._temperature = 0.2
        adapter._locale = "zh-CN"

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError("network error")
        adapter._client = fake_client

        with pytest.raises(LLMRuntimeError, match="RuntimeError"):
            adapter.analyze_structure("src1", _make_blocks())
