"""LLM adapter layer for Book2Skill.

Provides an abstraction over LLM-driven analysis so the pipeline can run
offline with a rule-based :class:`~book2skill.llm.mock_adapter.MockLLMAdapter`
(M1) or against an OpenAI-compatible endpoint (P1).
"""

from book2skill.llm.mock_adapter import MockLLMAdapter
from book2skill.llm.openai_adapter import OpenAIAdapter
from book2skill.llm.ports import LLMAdapter
from book2skill.llm.runtime import (
    AnalysisRunManifest,
    LLMQuotaExceededError,
    LLMRuntimeConfig,
    LLMRuntimeError,
    RuntimeLLMAdapter,
    build_llm_adapter,
    resolve_runtime_config,
)

__all__ = [
    "AnalysisRunManifest",
    "LLMAdapter",
    "LLMQuotaExceededError",
    "LLMRuntimeConfig",
    "LLMRuntimeError",
    "MockLLMAdapter",
    "OpenAIAdapter",
    "RuntimeLLMAdapter",
    "build_llm_adapter",
    "resolve_runtime_config",
]
