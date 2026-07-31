"""Tests for the OpenAI-compatible LLM adapter (PRD P1).

The ``openai`` package is not installed in the test environment, so these
tests mock the client to verify prompt construction, response parsing, and
graceful fallback to the MockLLMAdapter.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from book2skill.domain import Locator, LocatorKind, TextBlock
from book2skill.llm.mock_adapter import MockLLMAdapter
from book2skill.llm.openai_adapter import OpenAIAdapter, _parse_json_array


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
    """Fallback behaviour when openai is not installed."""

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

    def test_falls_back_to_mock_when_unavailable(self) -> None:
        adapter = OpenAIAdapter()
        blocks = _make_blocks()
        # Should produce same output as the mock adapter.
        mock = MockLLMAdapter()
        assert (
            adapter.analyze_structure("src1", blocks)
            == mock.analyze_structure("src1", blocks)
        )
        assert (
            adapter.extract_candidates("src1", blocks)
            == mock.extract_candidates("src1", blocks)
        )


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
        adapter._mock = MockLLMAdapter()

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

    def test_llm_returns_malformed_falls_back_to_mock(self) -> None:
        adapter = self._make_adapter_with_mock_client("not valid json")
        blocks = _make_blocks()
        mock = MockLLMAdapter()
        result = adapter.extract_candidates("src1", blocks)
        # Falls back to mock output.
        assert result == mock.extract_candidates("src1", blocks)

    def test_llm_exception_falls_back_to_mock(self) -> None:
        adapter = OpenAIAdapter.__new__(OpenAIAdapter)
        adapter._model = "test-model"
        adapter._api_key = "sk-test"
        adapter._base_url = None
        adapter._mock = MockLLMAdapter()

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError("network error")
        adapter._client = fake_client

        blocks = _make_blocks()
        mock = MockLLMAdapter()
        result = adapter.analyze_structure("src1", blocks)
        assert result == mock.analyze_structure("src1", blocks)
