"""Tests for redacted local LLM timing history and ETA estimates."""

from __future__ import annotations

import json

from book2skill.llm.performance import (
    ChunkTimingHistory,
    ChunkTimingSample,
    estimate_eta,
)


def _sample(tokens: int, seconds: float) -> ChunkTimingSample:
    return ChunkTimingSample(
        operation="chunk",
        provider="openai",
        model="demo",
        runtime_scope="a" * 64,
        prompt_version="analysis-v1",
        response_schema_version="analysis-response-v1",
        input_tokens=tokens,
        duration_seconds=seconds,
    )


def test_timing_history_persists_only_redacted_metrics(tmp_path) -> None:
    path = tmp_path / "performance-history.json"
    history = ChunkTimingHistory(path)
    history.append(_sample(100, 2.0))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["samples"] == [
        {
            "operation": "chunk",
            "provider": "openai",
            "model": "demo",
            "runtime_scope": "a" * 64,
            "prompt_version": "analysis-v1",
            "response_schema_version": "analysis-response-v1",
            "input_tokens": 100,
            "duration_seconds": 2.0,
        }
    ]
    restored = ChunkTimingHistory(path)
    assert restored.matching(
        operation="chunk",
        provider="openai",
        model="demo",
        runtime_scope="a" * 64,
        prompt_version="analysis-v1",
        response_schema_version="analysis-response-v1",
    ) == [_sample(100, 2.0)]


def test_eta_uses_median_token_rate_and_interval() -> None:
    estimate = estimate_eta(
        100, [_sample(100, 1.0), _sample(100, 2.0), _sample(100, 8.0)]
    )

    assert estimate is not None
    assert estimate.seconds == 2.0
    assert estimate.lower_seconds == 1.0
    assert estimate.upper_seconds == 2.0
    assert estimate.sample_count == 3


def test_eta_returns_none_without_valid_samples() -> None:
    assert estimate_eta(100, []) is None
    assert estimate_eta(0, [_sample(100, 1.0)]) is None
