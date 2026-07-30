"""LLM adapter layer for Book2Skill.

Provides an abstraction over LLM-driven analysis so the pipeline can run
offline with a rule-based :class:`~book2skill.llm.mock_adapter.MockLLMAdapter`
(M1) or against an OpenAI-compatible endpoint (P1).
"""

from book2skill.llm.mock_adapter import MockLLMAdapter
from book2skill.llm.openai_adapter import OpenAIAdapter
from book2skill.llm.ports import LLMAdapter

__all__ = ["LLMAdapter", "MockLLMAdapter", "OpenAIAdapter"]
