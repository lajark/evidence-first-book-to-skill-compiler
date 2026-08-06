"""Tests for explicit LLM runtime policy and redacted audit manifests."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from book2skill.domain import Locator, LocatorKind, TextBlock
from book2skill.llm.chunking import ChunkItem
from book2skill.llm.runtime import (
    ChunkProgressEvent,
    LLMRuntimeConfig,
    LLMRuntimeError,
    LLMUnavailableError,
    RuntimeLLMAdapter,
    default_timing_history_path,
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


class _TelemetryProvider:
    last_chat_telemetry = {
        "streaming": True,
        "stream_fallback": False,
        "first_token_latency_seconds": 0.25,
        "output_tokens": 12,
        "output_tokens_per_second": 48.0,
    }

    def extract_candidates(
        self, source_id: str, blocks: list[TextBlock]
    ) -> list[dict[str, object]]:
        del source_id, blocks
        return []


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


class _RetryingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        self.calls += 1
        if self.calls == 1:
            raise LLMRuntimeError("temporary network failure")
        return {"structure": [], "candidates": []}


class _SlowProvider:
    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        time.sleep(0.01)
        return {"structure": [], "candidates": []}


class _HeartbeatProvider:
    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        time.sleep(0.7)
        return {"structure": [], "candidates": []}

    def suggest_skills(
        self, source_id: str, candidates: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        time.sleep(0.7)
        return []


class _SynthesisProvider:
    def __init__(self) -> None:
        self.calls = 0

    def synthesize_candidates(
        self,
        source_id: str,
        level: str,
        candidates: list[dict[str, object]],
        structure: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        del structure
        self.calls += 1
        return [
            {
                "kind": "framework",
                "content": f"{level} synthesis",
                "confidence": 0.8,
                "source_unit_ids": [str(candidates[0]["unit_id"])],
            }
        ]


def test_real_runtime_requires_credentials() -> None:
    with pytest.raises(LLMUnavailableError, match="requires LLM_API_KEY"):
        LLMRuntimeConfig(provider="openai", model="demo")


def test_runtime_manifest_records_streaming_telemetry() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(
            provider="openai", model="demo", api_key="test", streaming=True
        )
    )
    adapter._provider = _TelemetryProvider()  # noqa: SLF001 - telemetry seam

    adapter.extract_candidates("source", _blocks())

    invocation = adapter.manifest.invocations[-1]
    assert invocation.streaming is True
    assert invocation.stream_fallback is False
    assert invocation.first_token_latency_seconds == 0.25
    assert invocation.output_tokens == 12
    assert invocation.output_tokens_per_second == 48.0


def test_default_timing_history_uses_platform_local_cache() -> None:
    path = default_timing_history_path(
        {"LOCALAPPDATA": r"C:\Users\demo\AppData\Local"}
    )

    assert path == (
        Path(r"C:\Users\demo\AppData\Local")
        / "book2skill"
        / "performance-history.json"
    )


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
            "BOOK2SKILL_LLM_STREAMING": "true",
        },
    )

    assert config.max_concurrent_requests == 2
    assert config.requests_per_minute == 120
    assert config.request_timeout_seconds == 12.5
    assert config.streaming is True


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


def test_chunk_runtime_persists_redacted_timing_and_reuses_it_for_eta(tmp_path) -> None:
    cache_root = tmp_path / "llm"
    item = ChunkItem(
        "input",
        "block",
        "Sensitive source sentence must not enter timing history.",
        _blocks()[0].locator,
    )
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(provider="openai", model="demo", api_key="test"),
        cache_root=cache_root,
    )
    adapter._provider = _SlowProvider()  # noqa: SLF001 - deterministic timing seam

    adapter.analyze_chunk("source", [item], extractor_id="text-v1")

    history_path = cache_root.parent / "performance-history.json"
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    assert len(payload["samples"]) == 1
    assert item.text not in history_path.read_text(encoding="utf-8")
    assert adapter.estimate_chunk_eta([item]) is not None

    adapter.analyze_chunk("source", [item], extractor_id="text-v1")
    assert len(json.loads(history_path.read_text(encoding="utf-8"))["samples"]) == 1


def test_synthesis_cache_is_content_addressed_and_persistent(tmp_path) -> None:
    config = LLMRuntimeConfig(
        provider="openai",
        model="demo",
        api_key="test",
        max_retries=0,
        requests_per_minute=60_000,
    )
    payload = [{"unit_id": "cu-1", "content": "A bounded evidence card."}]
    provider = _SynthesisProvider()
    first = RuntimeLLMAdapter(config, cache_root=tmp_path)
    first._provider = provider  # noqa: SLF001 - deterministic cache seam
    expected = first.synthesize_candidates("source", "chapter", payload)

    second = RuntimeLLMAdapter(config, cache_root=tmp_path)
    second._provider = provider  # noqa: SLF001 - deterministic cache seam
    assert second.synthesize_candidates("source", "chapter", payload) == expected
    assert provider.calls == 1
    assert second.manifest.invocations[-1].operation == "synthesis"
    assert second.manifest.invocations[-1].reason == "cache_hit"


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


def test_chunk_scheduler_reports_each_completion_without_reordering_results() -> None:
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
    completions: list[tuple[int, int, int]] = []

    with ThreadPoolExecutor(max_workers=1) as invoker:
        future = invoker.submit(
            adapter.analyze_chunks,
            requests,
            on_completed=lambda done, total, index: completions.append(
                (done, total, index)
            ),
        )
        assert provider.two_started.wait(timeout=1)
        provider.release.set()
        assert future.result(timeout=2) == [
            {"structure": [], "candidates": []}
            for _ in requests
        ]

    assert [done for done, _, _ in completions] == [1, 2, 3]
    assert {total for _, total, _ in completions} == {3}
    assert {index for _, _, index in completions} == {0, 1, 2}


def test_chunk_scheduler_does_not_advance_failed_request() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(
            provider="openai",
            model="demo",
            api_key="test",
            max_retries=1,
        )
    )
    adapter._provider = _FailingProvider()  # noqa: SLF001 - failure seam
    item = ChunkItem("input", "block", "text", _blocks()[0].locator)
    completions: list[tuple[int, int, int]] = []

    with pytest.raises(LLMRuntimeError, match="network unavailable"):
        adapter.analyze_chunks(
            [("source", [item], "text-v1")],
            on_completed=lambda done, total, index: completions.append(
                (done, total, index)
            ),
        )

    assert completions == []


def test_chunk_scheduler_counts_cache_hit_once(tmp_path) -> None:
    config = LLMRuntimeConfig.mock()
    item = ChunkItem("input", "block", "text", _blocks()[0].locator)
    request = [("source", [item], "text-v1")]
    RuntimeLLMAdapter(config, cache_root=tmp_path).analyze_chunks(request)
    completions: list[tuple[int, int, int]] = []

    cached = RuntimeLLMAdapter(config, cache_root=tmp_path)
    cached.analyze_chunks(
        request,
        on_completed=lambda done, total, index: completions.append(
            (done, total, index)
        ),
    )

    assert completions == [(1, 1, 0)]
    assert cached.manifest.invocations[-1].reason == "cache_hit"


def test_single_real_chunk_emits_caller_thread_heartbeats_before_completion() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(provider="openai", model="demo", api_key="test")
    )
    adapter._provider = _HeartbeatProvider()  # noqa: SLF001 - timing seam
    item = ChunkItem("input", "block", "text", _blocks()[0].locator)
    caller_thread = threading.get_ident()
    statuses: list[tuple[float, bool, int]] = []

    adapter.analyze_chunks(
        [("source", [item], "text-v1")],
        on_status=lambda event: statuses.append(
            (event.elapsed_seconds, event.heartbeat, threading.get_ident())
        ),
    )

    heartbeats = [elapsed for elapsed, heartbeat, _ in statuses if heartbeat]
    assert len(heartbeats) >= 2
    assert heartbeats == sorted(heartbeats)
    assert all(elapsed > 0 for elapsed in heartbeats)
    assert {thread_id for _, _, thread_id in statuses} == {caller_thread}


def test_non_streaming_skill_request_emits_heartbeats_and_records_timing() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(provider="openai", model="demo", api_key="test")
    )
    adapter._provider = _HeartbeatProvider()  # noqa: SLF001 - timing seam
    events: list[ChunkProgressEvent] = []

    assert adapter.suggest_skills_with_progress(
        "source",
        [{"name": "demo", "description": "A sufficiently long description."}],
        on_status=events.append,
    ) == []

    assert len([event for event in events if event.heartbeat]) >= 2
    estimate = adapter.estimate_input_eta(100, operation="skills")
    assert estimate is not None
    assert estimate.sample_count == 1


def test_real_runtime_cold_start_returns_broad_labelled_estimate() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(
            provider="openai",
            model="demo",
            api_key="test",
            request_timeout_seconds=30,
            max_retries=2,
        )
    )

    estimate = adapter.estimate_input_eta(800)

    assert estimate is not None
    assert estimate.sample_count == 0
    assert estimate.lower_seconds < estimate.seconds < estimate.upper_seconds
    assert estimate.upper_seconds == 90


def test_chunk_scheduler_reports_cache_and_retry_statuses_on_caller_thread(
    tmp_path,
) -> None:
    item = ChunkItem("input", "block", "text", _blocks()[0].locator)
    caller_thread = threading.get_ident()
    cached_statuses: list[tuple[str, int]] = []
    config = LLMRuntimeConfig.mock()
    request = [("source", [item], "text-v1")]
    RuntimeLLMAdapter(config, cache_root=tmp_path).analyze_chunks(request)
    RuntimeLLMAdapter(config, cache_root=tmp_path).analyze_chunks(
        request,
        on_status=lambda event: cached_statuses.append(
            (event.status, threading.get_ident())
        ),
    )

    assert cached_statuses == [("cached", caller_thread)]

    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(
            provider="openai", model="demo", api_key="test", max_retries=1
        )
    )
    adapter._provider = _RetryingProvider()  # noqa: SLF001 - retry seam
    retry_statuses: list[tuple[str, int]] = []
    adapter.analyze_chunks(
        request,
        on_status=lambda event: retry_statuses.append(
            (event.status, threading.get_ident())
        ),
    )

    assert [
        status for status, _ in retry_statuses if status in {"running", "retrying"}
    ] == [
        "running",
        "retrying",
        "running",
    ]
    assert {thread_id for _, thread_id in retry_statuses} == {caller_thread}


def test_chunk_scheduler_reports_rate_limit_wait_on_caller_thread() -> None:
    adapter = RuntimeLLMAdapter(
        LLMRuntimeConfig(
            provider="openai",
            model="demo",
            api_key="test",
            max_concurrent_requests=2,
            requests_per_minute=600,
        )
    )
    adapter._provider = _TimestampProvider()  # noqa: SLF001 - scheduler seam
    item = ChunkItem("input", "block", "text", _blocks()[0].locator)
    caller_thread = threading.get_ident()
    statuses: list[tuple[str, int]] = []

    adapter.analyze_chunks(
        [(f"source-{index}", [item], "text-v1") for index in range(2)],
        on_status=lambda event: statuses.append((event.status, threading.get_ident())),
    )

    assert "rate_limited" in [status for status, _ in statuses]
    assert {thread_id for _, thread_id in statuses} == {caller_thread}


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
