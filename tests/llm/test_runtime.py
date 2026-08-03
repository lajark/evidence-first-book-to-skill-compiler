"""Tests for explicit LLM runtime policy and redacted audit manifests."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from book2skill.domain import Locator, LocatorKind, TextBlock
from book2skill.llm.chunking import ChunkItem
from book2skill.llm.runtime import (
    LLMRuntimeConfig,
    LLMRuntimeError,
    LLMUnavailableError,
    RuntimeLLMAdapter,
    resolve_runtime_config,
)


def _blocks() -> list[TextBlock]:
    return [
        TextBlock(
            text="A principle should remain easy to audit.",
            locator=Locator(kind=LocatorKind.PARAGRAPH, paragraph=1),
        )
    ]


class _FailingProvider:
    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        raise LLMRuntimeError("network unavailable")

    def analyze_structure(
        self, source_id: str, blocks: list[TextBlock]
    ) -> list[dict[str, object]]:
        raise LLMRuntimeError("network unavailable")

    def extract_candidates(
        self, source_id: str, blocks: list[TextBlock]
    ) -> list[dict[str, object]]:
        raise LLMRuntimeError("network unavailable")

    def suggest_skills(
        self, source_id: str, candidates: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        raise LLMRuntimeError("network unavailable")


class _BlockingProvider:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.two_started = threading.Event()
        self.release = threading.Event()

    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active >= 2:
                self.two_started.set()
        assert self.release.wait(timeout=2)
        with self.lock:
            self.active -= 1
        return {"structure": [], "candidates": []}


class _TimestampProvider:
    def __init__(self) -> None:
        self.starts: list[float] = []
        self.lock = threading.Lock()

    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        with self.lock:
            self.starts.append(time.monotonic())
        return {"structure": [], "candidates": []}


def test_real_runtime_requires_credentials() -> None:
    with pytest.raises(LLMUnavailableError, match="requires LLM_API_KEY"):
        LLMRuntimeConfig(provider="openai", model="demo")


def test_real_runtime_fails_closed_without_fallback() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(provider="openai", model="demo", api_key="test")
    )
    adapter._provider = _FailingProvider()  # noqa: SLF001 - deterministic seam

    with pytest.raises(LLMRuntimeError, match="network unavailable"):
        adapter.analyze_structure("source", _blocks())

    event = adapter.manifest.invocations[-1]
    assert event.outcome == "error"
    assert event.reason == "network unavailable"
    assert event.response_sha256 is None


def test_explicit_fallback_is_recorded_without_source_or_secret() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(
            provider="openai",
            model="demo",
            api_key="top-secret",
            allow_fallback=True,
        )
    )
    adapter._provider = _FailingProvider()  # noqa: SLF001 - deterministic seam

    result = adapter.extract_candidates("source", _blocks())

    assert result
    manifest = adapter.manifest.model_dump_json()
    assert '"fallback"' in manifest
    assert "network unavailable" in manifest
    assert "top-secret" not in manifest
    assert "A principle should" not in manifest


def test_resolver_uses_generic_environment_values() -> None:
    config = resolve_runtime_config(
        "compatible",
        environment={
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "local-model",
        },
    )

    assert config.provider == "openai"
    assert config.model == "local-model"


def test_resolver_reads_llm_scheduler_environment_values() -> None:
    config = resolve_runtime_config(
        "compatible",
        environment={
            "LLM_API_KEY": "test-key",
            "BOOK2SKILL_LLM_MAX_CONCURRENT_REQUESTS": "2",
            "BOOK2SKILL_LLM_REQUESTS_PER_MINUTE": "120",
            "BOOK2SKILL_LLM_REQUEST_TIMEOUT_SECONDS": "12.5",
        },
    )

    assert config.max_concurrent_requests == 2
    assert config.requests_per_minute == 120
    assert config.request_timeout_seconds == 12.5


def test_chunk_runtime_retries_then_opens_circuit() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(
            provider="openai",
            model="demo",
            api_key="test",
            max_retries=1,
            circuit_failure_threshold=2,
        )
    )
    adapter._provider = _FailingProvider()  # noqa: SLF001
    item = ChunkItem("input", "block", "text", _blocks()[0].locator)

    with pytest.raises(LLMRuntimeError, match="network unavailable"):
        adapter.analyze_chunk("source", [item])
    with pytest.raises(LLMRuntimeError, match="network unavailable"):
        adapter.analyze_chunk("source", [item])
    with pytest.raises(LLMRuntimeError, match="circuit is open"):
        adapter.analyze_chunk("source", [item])


def test_chunk_cache_is_content_addressed_and_persistent(tmp_path) -> None:
    item = ChunkItem(
        "input", "block", "A principle should be cached.", _blocks()[0].locator
    )
    config = LLMRuntimeConfig.mock()
    first = RuntimeLLMAdapter(config, cache_root=tmp_path)
    expected = first.analyze_chunk("source", [item], extractor_id="text-v1")
    assert list(tmp_path.glob("*.json"))

    second = RuntimeLLMAdapter(config, cache_root=tmp_path)
    assert second.analyze_chunk("source", [item], extractor_id="text-v1") == expected
    assert second.manifest.invocations[-1].reason == "cache_hit"
    second.analyze_chunk("source", [item], extractor_id="other-text-v1")
    assert second.manifest.invocations[-1].reason is None


def test_chunk_scheduler_bounds_real_provider_concurrency_and_keeps_order() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(
            provider="openai",
            model="demo",
            api_key="test",
            max_concurrent_requests=2,
            requests_per_minute=60_000,
        )
    )
    provider = _BlockingProvider()
    adapter._provider = provider  # noqa: SLF001 - deterministic scheduler seam
    item = ChunkItem("input", "block", "text", _blocks()[0].locator)
    requests = [(f"source-{index}", [item], "text-v1") for index in range(3)]

    with ThreadPoolExecutor(max_workers=1) as invoker:
        future = invoker.submit(adapter.analyze_chunks, requests)
        assert provider.two_started.wait(timeout=1)
        provider.release.set()
        assert future.result(timeout=2) == [
            {"structure": [], "candidates": []}
            for _ in requests
        ]

    assert provider.max_active == 2


def test_chunk_scheduler_rate_limits_real_provider_request_starts() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(
            provider="openai",
            model="demo",
            api_key="test",
            max_concurrent_requests=3,
            requests_per_minute=6_000,
        )
    )
    provider = _TimestampProvider()
    adapter._provider = provider  # noqa: SLF001 - deterministic scheduler seam
    item = ChunkItem("input", "block", "text", _blocks()[0].locator)
    adapter.analyze_chunks(
        [(f"source-{index}", [item], "text-v1") for index in range(3)]
    )

    starts = sorted(provider.starts)
    assert len(starts) == 3
    assert starts[1] - starts[0] >= 0.005
