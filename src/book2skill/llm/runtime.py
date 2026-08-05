"""Resolved, auditable LLM runtime configuration.

The application layer receives one :class:`LLMRuntimeConfig` regardless of
whether it is entered through Analyze, Build, Update, or Batch.  Real-model
failures are fail-closed by default.  A caller must explicitly opt in to the
deterministic Mock fallback, and every invocation records only metadata and a
digest/size summary of the response -- never prompts, source text, or keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from book2skill.config import Locale, resolve_locale
from book2skill.domain import TextBlock
from book2skill.llm.chunking import ChunkItem, estimate_tokens
from book2skill.llm.performance import (
    ChunkTimingHistory,
    ChunkTimingSample,
    EtaEstimate,
    TimingOperation,
    estimate_eta,
)
from book2skill.llm.ports import LLMAdapter

Provider = Literal["mock", "openai"]
InvocationOutcome = Literal["success", "fallback", "error"]
ChunkStatus = Literal[
    "running", "cached", "rate_limited", "retrying", "failed", "fallback"
]

# Routing-strategy version included in cache scope so multi-channel routing
# (OPT-P1-09) can invalidate caches without changing profile content. The
# single-channel path keeps this default; a future router bumps it when its
# selection semantics change in a way that must invalidate prior caches.
ROUTING_STRATEGY_VERSION = "single-v1"


@dataclass(frozen=True)
class ChunkProgressEvent:
    """A redacted runtime-status update for one scheduled LLM chunk."""

    index: int
    status: ChunkStatus
    attempt: int = 0
    wait_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    heartbeat: bool = False


class LLMRuntimeError(RuntimeError):
    """Base error for unavailable, failed, or invalid real-model calls."""


class LLMUnavailableError(LLMRuntimeError):
    """Raised when a configured real provider cannot be initialized."""


class LLMResponseError(LLMRuntimeError):
    """Raised when a real provider response cannot satisfy the contract."""


@dataclass(frozen=True)
class LLMRuntimeConfig:
    """All non-secret settings that identify an analysis runtime."""

    provider: Provider = "mock"
    model: str = "mock-rule-based-v1"
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False, compare=False)
    allow_fallback: bool = False
    temperature: float = 0.2
    prompt_version: str = "analysis-v4"
    response_schema_version: str = "analysis-response-v2"
    locale: Locale = "zh-CN"
    max_retries: int = 2
    circuit_failure_threshold: int = 3
    max_concurrent_requests: int = 2
    requests_per_minute: int = 15
    request_timeout_seconds: float = 30.0
    # Profile/routing provenance. ``profile_id`` identifies the channel a
    # run was routed to (None for the legacy single-provider path); the
    # routing strategy version namespaces the content cache so multi-channel
    # routing (OPT-P1-09) cannot reuse results across profiles/strategies.
    # ``data_send_policy`` is the redacted profile policy recorded for audit.
    profile_id: str | None = None
    routing_strategy_version: str = ROUTING_STRATEGY_VERSION
    data_send_policy: str | None = None

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("LLM model must not be empty")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("LLM temperature must be between 0.0 and 2.0")
        if self.max_retries < 0 or self.circuit_failure_threshold < 1:
            raise ValueError("LLM retry and circuit thresholds are invalid")
        if self.max_concurrent_requests < 1 or self.requests_per_minute < 1:
            raise ValueError("LLM scheduler limits must be positive")
        if self.request_timeout_seconds <= 0:
            raise ValueError("LLM request timeout must be positive")
        if self.locale not in {"zh-CN", "en"}:
            raise ValueError("LLM locale must be zh-CN or en")
        if self.provider == "openai" and not self.api_key:
            raise LLMUnavailableError(
                "A real LLM provider requires LLM_API_KEY or OPENAI_API_KEY. "
                "Use --llm mock for offline analysis."
            )

    @classmethod
    def mock(cls) -> LLMRuntimeConfig:
        """Return the explicit deterministic offline runtime."""
        return cls()


class LLMInvocation(BaseModel):
    """Redacted audit event for a single model operation."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal[
        "structure", "candidates", "skills", "chunk", "synthesis", "review"
    ]
    outcome: InvocationOutcome
    provider: Provider
    model: str
    prompt_version: str
    response_schema_version: str
    parameters: dict[str, float | int | str | bool]
    reason: str | None = None
    response_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    response_items: int | None = Field(default=None, ge=0)
    # Channel that served this invocation (None for the single-provider path).
    # Redacted identifier only; never a credential or endpoint secret.
    profile_id: str | None = None


class AnalysisRunManifest(BaseModel):
    """Portable audit metadata for the LLM portion of one Analyze run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    provider: Provider
    model: str
    prompt_version: str
    response_schema_version: str
    locale: Literal["zh-CN", "en"] = "zh-CN"
    parameters: dict[str, float | int | str | bool]
    fallback_allowed: bool
    invocations: list[LLMInvocation] = Field(default_factory=list)
    # Redacted profile/routing provenance. Optional so legacy manifests without
    # these fields still validate; no credential, endpoint, prompt or text is
    # ever recorded here.
    profile_id: str | None = None
    data_send_policy: str | None = None


class RuntimeLLMAdapter:
    """Wrap a provider with explicit fallback policy and redacted auditing."""

    def __init__(
        self,
        config: LLMRuntimeConfig,
        *,
        cache_root: Path | None = None,
        timing_history_path: Path | None = None,
    ) -> None:
        self._config = config
        self._invocations: list[LLMInvocation] = []
        self._consecutive_failures = 0
        self._cache_root = cache_root
        history_path = timing_history_path or (
            cache_root.parent / "performance-history.json" if cache_root else None
        )
        self._timing_history = ChunkTimingHistory(history_path)
        self._timing_lock = threading.Lock()
        self._chunk_cache: dict[str, dict[str, list[dict[str, object]]]] = {}
        self._cache_lock = threading.Lock()
        self._invocation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._next_request_start = 0.0
        from book2skill.llm.mock_adapter import MockLLMAdapter

        self._fallback = MockLLMAdapter()
        if config.provider == "mock":
            self._provider: LLMAdapter = self._fallback
        else:
            from book2skill.llm.openai_adapter import OpenAIAdapter

            self._provider = OpenAIAdapter(
                model=config.model,
                api_key=config.api_key,
                base_url=config.base_url,
                temperature=config.temperature,
                request_timeout_seconds=config.request_timeout_seconds,
                locale=config.locale,
            )

    @property
    def config(self) -> LLMRuntimeConfig:
        """Return the effective, secret-redacted configuration carrier."""
        return self._config

    @property
    def manifest(self) -> AnalysisRunManifest:
        """Return a snapshot suitable for serialisation with a bundle."""
        with self._invocation_lock:
            invocations = list(self._invocations)
        return AnalysisRunManifest(
            provider=self._config.provider,
            model=self._config.model,
            prompt_version=self._config.prompt_version,
            response_schema_version=self._config.response_schema_version,
            locale=self._config.locale,
            parameters=self._audit_parameters(),
            fallback_allowed=self._config.allow_fallback,
            invocations=invocations,
            profile_id=self._config.profile_id,
            data_send_policy=self._config.data_send_policy,
        )

    def start_run(self) -> None:
        """Clear prior invocation records before an independent Analyze run."""
        with self._state_lock:
            self._consecutive_failures = 0
        with self._invocation_lock:
            self._invocations.clear()
    def estimate_chunk_eta(self, items: list[ChunkItem]) -> EtaEstimate | None:
        """Predict one uncached chunk from matching history and this run."""
        return self.estimate_input_eta(_chunk_input_tokens(items), operation="chunk")

    def estimate_input_eta(
        self,
        input_tokens: int,
        *,
        operation: TimingOperation = "chunk",
    ) -> EtaEstimate | None:
        """Predict one real-model request, with a labelled cold-start prior."""
        with self._timing_lock:
            estimate = estimate_eta(
                input_tokens, self._matching_timing_samples(operation)
            )
        if estimate is not None or self._config.provider != "openai":
            return estimate
        if input_tokens <= 0:
            return None
        point = min(
            self._config.request_timeout_seconds * 0.8,
            max(5.0, 4.0 + input_tokens / 40.0),
        )
        return EtaEstimate(
            seconds=point,
            lower_seconds=max(2.0, point * 0.5),
            upper_seconds=max(
                point * 2.0,
                self._config.request_timeout_seconds
                * (self._config.max_retries + 1),
            ),
            sample_count=0,
        )

    def analyze_structure(
        self, source_id: str, blocks: list[TextBlock]
    ) -> list[dict[str, object]]:
        return self._invoke("structure", source_id, blocks)

    def extract_candidates(
        self, source_id: str, blocks: list[TextBlock]
    ) -> list[dict[str, object]]:
        return self._invoke("candidates", source_id, blocks)

    def suggest_skills(
        self, source_id: str, candidates: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        return self._invoke("skills", source_id, candidates)

    def suggest_skills_with_progress(
        self,
        source_id: str,
        candidates: list[dict[str, object]],
        *,
        on_status: Callable[[ChunkProgressEvent], None] | None = None,
    ) -> list[dict[str, object]]:
        """Run the non-streaming skills request with caller-thread heartbeats."""
        if self._config.provider == "mock":
            return self.suggest_skills(source_id, candidates)
        input_tokens = estimate_tokens(
            json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))
        )
        started = time.monotonic()
        if on_status is not None:
            on_status(ChunkProgressEvent(index=0, status="running", attempt=1))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.suggest_skills, source_id, candidates)
            while True:
                done, _ = wait({future}, timeout=0.25, return_when=FIRST_COMPLETED)
                if done:
                    break
                if on_status is not None:
                    on_status(
                        ChunkProgressEvent(
                            index=0,
                            status="running",
                            attempt=1,
                            elapsed_seconds=time.monotonic() - started,
                            heartbeat=True,
                        )
                    )
            result = future.result()
        self._record_input_timing(
            input_tokens, time.monotonic() - started, operation="skills"
        )
        return result

    def synthesize_candidates(
        self,
        source_id: str,
        level: Literal["chapter", "book"],
        candidates: list[dict[str, object]],
        structure: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        """Consolidate bounded evidence cards with a persistent cache.

        A synthesis request only contains prior candidate cards, never raw
        source text.  A completed chapter reduction can therefore be reused
        after interruption without resending a book chapter to the provider.

        ``structure`` is an optional organizing anchor (detected section
        headings) passed through to the provider; it is part of the cache
        identity so a changed structure never reuses a stale reduction.
        """
        cache_key = self._synthesis_cache_key(
            source_id, level, candidates, structure
        )
        cached = self._load_cached_synthesis(cache_key)
        if cached is not None:
            self._record("synthesis", "success", cached, "cache_hit")
            return cached
        input_tokens = max(
            1,
            estimate_tokens(
                json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))
            ),
        )
        started = time.monotonic()
        try:
            result = self._retry_synthesis(source_id, level, candidates, structure)
        except LLMRuntimeError as exc:
            if self._config.provider == "openai" and self._config.allow_fallback:
                result = self._dispatch_synthesis(
                    self._fallback, source_id, level, candidates, structure
                )
                self._record("synthesis", "fallback", result, str(exc))
                return result
            self._record("synthesis", "error", None, str(exc))
            raise
        self._record_input_timing(
            input_tokens, time.monotonic() - started, operation="synthesis"
        )
        self._store_cached_synthesis(cache_key, result)
        self._record("synthesis", "success", result, None)
        return result

    def review_evidence(self, card: dict[str, object]) -> dict[str, object]:
        """Run one Critic/Arbiter review of an evidence card.

        Dispatches to the provider's ``review_evidence`` and records the
        outcome. The card is already anonymized (no model identity, prompt,
        endpoint or source text beyond the extracted content).
        """
        reviewer = cast("_ReviewAdapter", self._provider)
        try:
            result = reviewer.review_evidence(card)
        except LLMRuntimeError as exc:
            if self._config.provider == "openai" and self._config.allow_fallback:
                result = cast("_ReviewAdapter", self._fallback).review_evidence(card)
                self._record("review", "fallback", None, str(exc))
                return result
            self._record("review", "error", None, str(exc))
            raise
        # The review patch body lives in the bundle's quality_review; the audit
        # records only the operation/outcome, never the reviewer's text.
        self._record("review", "success", None, None)
        return result

    def _retry_synthesis(
        self,
        source_id: str,
        level: Literal["chapter", "book"],
        candidates: list[dict[str, object]],
        structure: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        """Use the same start-rate budget and bounded retry policy as Map."""
        last_error: LLMRuntimeError | None = None
        for attempt in range(1, self._config.max_retries + 2):
            delay = self._reserve_request_slot()
            if delay > 0:
                time.sleep(delay)
            try:
                return self._dispatch_synthesis(
                    self._provider, source_id, level, candidates, structure
                )
            except LLMRuntimeError as exc:
                last_error = exc
                if attempt <= self._config.max_retries:
                    # Short exponential backoff with jitter prevents a cohort
                    # of failed reductions from retrying in lockstep.
                    time.sleep((2 ** (attempt - 1)) + (0.17 * attempt))
        assert last_error is not None
        raise last_error

    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem], *, extractor_id: str = "unknown"
    ) -> dict[str, list[dict[str, object]]]:
        """Run one merged chunk request under the same fallback policy."""
        return self._analyze_chunk(
            source_id, items, extractor_id=extractor_id, on_status=None
        )

    def _analyze_chunk(
        self,
        source_id: str,
        items: list[ChunkItem],
        *,
        extractor_id: str,
        on_status: Callable[[ChunkStatus, int, float], None] | None,
    ) -> dict[str, list[dict[str, object]]]:
        """Run one chunk, optionally reporting redacted lifecycle statuses."""
        cache_key = self._cache_key(source_id, items, extractor_id)
        cached = self._load_cached_chunk(cache_key)
        if cached is not None:
            if on_status is not None:
                on_status("cached", 0, 0.0)
            self._record("chunk", "success", cached, "cache_hit")
            return cached
        with self._state_lock:
            circuit_open = (
                self._consecutive_failures
                >= self._config.circuit_failure_threshold
            )
        if circuit_open:
            error = LLMRuntimeError("LLM circuit is open after consecutive failures")
            if on_status is not None:
                on_status("failed", 0, 0.0)
            self._record("chunk", "error", None, str(error))
            raise error
        try:
            started = time.monotonic()
            result = self._retry_chunk(source_id, items, on_status=on_status)
        except LLMRuntimeError as exc:
            with self._state_lock:
                self._consecutive_failures += 1
            if self._config.provider == "openai" and self._config.allow_fallback:
                if on_status is not None:
                    on_status("fallback", 0, 0.0)
                result = self._chunk_dispatch(self._fallback, source_id, items)
                self._record("chunk", "fallback", result, str(exc))
                return result
            if on_status is not None:
                on_status("failed", 0, 0.0)
            self._record("chunk", "error", None, str(exc))
            raise
        with self._state_lock:
            self._consecutive_failures = 0
        self._record_chunk_timing(items, time.monotonic() - started)
        self._store_cached_chunk(cache_key, result)
        self._record("chunk", "success", result, None)
        return result

    def analyze_chunks(
        self,
        requests: list[tuple[str, list[ChunkItem], str]],
        *,
        on_completed: Callable[[int, int, int], None] | None = None,
        on_status: Callable[[ChunkProgressEvent], None] | None = None,
    ) -> list[dict[str, list[dict[str, object]]]]:
        """Run deterministic chunk requests with bounded real-provider concurrency.

        Results retain input order even when requests finish out of order. The
        offline Mock is deliberately kept sequential: it is CPU-local and that
        preserves its audit ordering without sacrificing network throughput.
        Both callbacks run on the scheduler's calling thread. ``on_completed``
        follows each successful or cached result; retries and failed attempts
        never advance the completed count. ``on_status`` carries only redacted
        scheduling state, never source text or prompts.
        """
        if not requests:
            return []
        total = len(requests)

        def direct_status_reporter(
            index: int,
        ) -> Callable[[ChunkStatus, int, float], None] | None:
            if on_status is None:
                return None

            def report(status: ChunkStatus, attempt: int, wait_seconds: float) -> None:
                on_status(
                    ChunkProgressEvent(
                        index=index,
                        status=status,
                        attempt=attempt,
                        wait_seconds=wait_seconds,
                    )
                )

            return report

        if self._config.provider == "mock":
            sequential_results: list[dict[str, list[dict[str, object]]]] = []
            for index, (source_id, items, extractor_id) in enumerate(requests):
                sequential_results.append(
                    self._analyze_chunk(
                        source_id,
                        items,
                        extractor_id=extractor_id,
                        on_status=direct_status_reporter(index),
                    )
                )
                if on_completed is not None:
                    on_completed(index + 1, total, index)
            return sequential_results

        results: list[dict[str, list[dict[str, object]]] | None] = [None] * len(
            requests
        )
        status_events: SimpleQueue[tuple[ChunkProgressEvent, float]] = SimpleQueue()

        def publish_status(
            index: int, status: ChunkStatus, attempt: int, wait_seconds: float
        ) -> None:
            status_events.put(
                (
                    ChunkProgressEvent(
                        index=index,
                        status=status,
                        attempt=attempt,
                        wait_seconds=wait_seconds,
                    ),
                    time.monotonic(),
                )
            )

        def queued_status_reporter(
            index: int,
        ) -> Callable[[ChunkStatus, int, float], None]:
            def report(status: ChunkStatus, attempt: int, wait_seconds: float) -> None:
                publish_status(index, status, attempt, wait_seconds)

            return report

        active_requests: dict[int, tuple[int, float]] = {}
        last_heartbeat: dict[int, float] = {}

        def flush_status_events() -> None:
            if on_status is None:
                return
            while True:
                try:
                    event, emitted_at = status_events.get_nowait()
                except Empty:
                    return
                if event.status == "running":
                    active_requests[event.index] = (event.attempt, emitted_at)
                    last_heartbeat[event.index] = 0.0
                elif event.status in {
                    "cached",
                    "rate_limited",
                    "retrying",
                    "failed",
                    "fallback",
                }:
                    active_requests.pop(event.index, None)
                on_status(event)

        with ThreadPoolExecutor(
            max_workers=min(self._config.max_concurrent_requests, total)
        ) as pool:
            futures = {
                pool.submit(
                    self._analyze_chunk,
                    source_id,
                    items,
                    extractor_id=extractor_id,
                    on_status=queued_status_reporter(index),
                ): index
                for index, (source_id, items, extractor_id) in enumerate(requests)
            }
            pending = set(futures)
            completed = 0
            while pending:
                done, pending = wait(
                    pending, timeout=0.1, return_when=FIRST_COMPLETED
                )
                flush_status_events()
                now = time.monotonic()
                if on_status is not None:
                    pending_indices = {futures[future] for future in pending}
                    for index, (attempt, started) in list(active_requests.items()):
                        elapsed = now - started
                        if (
                            index in pending_indices
                            and elapsed - last_heartbeat.get(index, 0.0) >= 0.25
                        ):
                            last_heartbeat[index] = elapsed
                            on_status(
                                ChunkProgressEvent(
                                    index=index,
                                    status="running",
                                    attempt=attempt,
                                    elapsed_seconds=elapsed,
                                    heartbeat=True,
                                )
                            )
                for future in done:
                    index = futures[future]
                    active_requests.pop(index, None)
                    results[index] = future.result()
                    completed += 1
                    if on_completed is not None:
                        on_completed(completed, total, index)
            flush_status_events()
        return [result for result in results if result is not None]

    def _cache_key(
        self, source_id: str, items: list[ChunkItem], extractor_id: str
    ) -> str:
        identity = {
            "source_id": source_id,
            "extractor": extractor_id,
            "provider": self._config.provider,
            "model": self._config.model,
            "prompt": self._config.prompt_version,
            "schema": self._config.response_schema_version,
            "temperature": self._config.temperature,
            "profile_id": self._config.profile_id,
            "routing_strategy": self._config.routing_strategy_version,
            "locale": self._config.locale,
            "items": [
                (item.input_id, item.source_block_id, item.text) for item in items
            ],
        }
        return hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _synthesis_cache_key(
        self,
        source_id: str,
        level: Literal["chapter", "book"],
        candidates: list[dict[str, object]],
        structure: list[dict[str, object]] | None = None,
    ) -> str:
        identity = {
            "source_id": source_id,
            "level": level,
            "provider": self._config.provider,
            "model": self._config.model,
            "prompt": self._config.prompt_version,
            "schema": self._config.response_schema_version,
            "temperature": self._config.temperature,
            "profile_id": self._config.profile_id,
            "routing_strategy": self._config.routing_strategy_version,
            "locale": self._config.locale,
            "candidates": candidates,
            "structure": structure,
        }
        return hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _matching_timing_samples(
        self, operation: TimingOperation
    ) -> list[ChunkTimingSample]:
        return self._timing_history.matching(
            operation=operation,
            provider=self._config.provider,
            model=self._config.model,
            runtime_scope=self._runtime_scope(),
            prompt_version=self._config.prompt_version,
            response_schema_version=self._config.response_schema_version,
        )

    def _record_chunk_timing(
        self, items: list[ChunkItem], duration_seconds: float
    ) -> None:
        self._record_input_timing(
            _chunk_input_tokens(items), duration_seconds, operation="chunk"
        )

    def _record_input_timing(
        self,
        input_tokens: int,
        duration_seconds: float,
        *,
        operation: TimingOperation,
    ) -> None:
        sample = ChunkTimingSample(
            operation=operation,
            provider=self._config.provider,
            model=self._config.model,
            runtime_scope=self._runtime_scope(),
            prompt_version=self._config.prompt_version,
            response_schema_version=self._config.response_schema_version,
            input_tokens=input_tokens,
            duration_seconds=duration_seconds,
        )
        if not sample.is_valid():
            return
        with self._timing_lock:
            self._timing_history.append(sample)

    def _runtime_scope(self) -> str:
        identity = self._config.base_url or self._config.provider
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def _load_cached_chunk(
        self, cache_key: str
    ) -> dict[str, list[dict[str, object]]] | None:
        with self._cache_lock:
            cached = self._chunk_cache.get(cache_key)
        if cached is not None:
            return cached
        if self._cache_root is None:
            return None
        path = self._cache_root / f"{cache_key}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or not all(
            isinstance(v, list) for v in value.values()
        ):
            return None
        result = {
            str(key): [dict(item) for item in values if isinstance(item, dict)]
            for key, values in value.items()
        }
        with self._cache_lock:
            self._chunk_cache[cache_key] = result
        return result

    def _load_cached_synthesis(
        self, cache_key: str
    ) -> list[dict[str, object]] | None:
        if self._cache_root is None:
            return None
        path = self._cache_root / f"synthesis-{cache_key}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, list) or any(
            not isinstance(item, dict) for item in value
        ):
            return None
        return [dict(item) for item in value]

    def _store_cached_chunk(
        self, cache_key: str, result: dict[str, list[dict[str, object]]]
    ) -> None:
        with self._cache_lock:
            self._chunk_cache[cache_key] = result
        if self._cache_root is None:
            return
        self._cache_root.mkdir(parents=True, exist_ok=True)
        target = self._cache_root / f"{cache_key}.json"
        temporary = target.with_name(f"{target.stem}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)

    def _store_cached_synthesis(
        self, cache_key: str, result: list[dict[str, object]]
    ) -> None:
        if self._cache_root is None:
            return
        self._cache_root.mkdir(parents=True, exist_ok=True)
        target = self._cache_root / f"synthesis-{cache_key}.json"
        temporary = target.with_name(f"{target.stem}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
    def _retry_chunk(
        self,
        source_id: str,
        items: list[ChunkItem],
        *,
        on_status: Callable[[ChunkStatus, int, float], None] | None,
    ) -> dict[str, list[dict[str, object]]]:
        last_error: LLMRuntimeError | None = None
        for attempt in range(1, self._config.max_retries + 2):
            try:
                delay = self._reserve_request_slot()
                if on_status is not None and delay > 0:
                    on_status("rate_limited", attempt, delay)
                if delay > 0:
                    time.sleep(delay)
                if on_status is not None:
                    on_status("running", attempt, 0.0)
                return self._chunk_dispatch(self._provider, source_id, items)
            except LLMRuntimeError as exc:
                last_error = exc
                if on_status is not None and attempt <= self._config.max_retries:
                    on_status("retrying", attempt + 1, 0.0)
        assert last_error is not None
        raise last_error

    def _reserve_request_slot(self) -> float:
        """Reserve a rate-limited request start and return its required delay."""
        if self._config.provider == "mock":
            return 0.0
        interval = 60.0 / self._config.requests_per_minute
        with self._rate_lock:
            now = time.monotonic()
            scheduled = max(now, self._next_request_start)
            self._next_request_start = scheduled + interval
        delay = scheduled - now
        return delay

    def _invoke(
        self,
        operation: Literal["structure", "candidates", "skills"],
        source_id: str,
        payload: list[TextBlock] | list[dict[str, object]],
    ) -> list[dict[str, object]]:
        # Retry transient failures (incl. malformed JSON) like the chunk path,
        # reserving a rate-limited start slot per attempt, before fallback.
        last_error: LLMRuntimeError | None = None
        for _attempt in range(1, self._config.max_retries + 2):
            try:
                delay = self._reserve_request_slot()
                if delay > 0:
                    time.sleep(delay)
                result = self._dispatch(
                    self._provider, operation, source_id, payload
                )
                self._record(operation, "success", result, None)
                return result
            except LLMRuntimeError as exc:
                last_error = exc
        assert last_error is not None
        if self._config.provider == "openai" and self._config.allow_fallback:
            result = self._dispatch(self._fallback, operation, source_id, payload)
            self._record(operation, "fallback", result, str(last_error))
            return result
        self._record(operation, "error", None, str(last_error))
        raise last_error

    @staticmethod
    def _dispatch(
        adapter: LLMAdapter,
        operation: Literal["structure", "candidates", "skills"],
        source_id: str,
        payload: list[TextBlock] | list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if operation == "structure":
            return adapter.analyze_structure(source_id, cast(list[TextBlock], payload))
        if operation == "candidates":
            return adapter.extract_candidates(source_id, cast(list[TextBlock], payload))
        return adapter.suggest_skills(source_id, cast(list[dict[str, object]], payload))

    @staticmethod
    def _chunk_dispatch(
        adapter: LLMAdapter, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        chunk_adapter = cast("_ChunkAdapter", adapter)
        return chunk_adapter.analyze_chunk(source_id, items)

    @staticmethod
    def _dispatch_synthesis(
        adapter: LLMAdapter,
        source_id: str,
        level: Literal["chapter", "book"],
        candidates: list[dict[str, object]],
        structure: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        method = getattr(adapter, "synthesize_candidates", None)
        if not callable(method):
            raise LLMRuntimeError("LLM adapter does not support hierarchical synthesis")
        result = method(source_id, level, candidates, structure)
        if not isinstance(result, list) or any(
            not isinstance(item, dict) for item in result
        ):
            raise LLMResponseError("LLM synthesis response must be an array of objects")
        return [dict(item) for item in result]

    def _record(
        self,
        operation: Literal[
        "structure", "candidates", "skills", "chunk", "synthesis", "review"
    ],
        outcome: InvocationOutcome,
        response: list[dict[str, object]] | dict[str, list[dict[str, object]]] | None,
        reason: str | None,
    ) -> None:
        summary = (
            json.dumps(
                response,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if response is not None
            else None
        )
        with self._invocation_lock:
            self._invocations.append(
                LLMInvocation(
                    operation=operation,
                    outcome=outcome,
                    provider=self._config.provider,
                    model=self._config.model,
                    prompt_version=self._config.prompt_version,
                    response_schema_version=self._config.response_schema_version,
                    parameters=self._audit_parameters(),
                    reason=reason,
                    response_sha256=(
                        hashlib.sha256(summary.encode("utf-8")).hexdigest()
                        if summary is not None
                        else None
                    ),
                    response_items=(
                        len(response)
                        if isinstance(response, list)
                        else sum(len(items) for items in response.values())
                        if response is not None
                        else None
                    ),
                    profile_id=self._config.profile_id,
                )
            )

    def _audit_parameters(self) -> dict[str, float | int | str | bool]:
        return {
            "temperature": self._config.temperature,
            "max_retries": self._config.max_retries,
            "max_concurrent_requests": self._config.max_concurrent_requests,
            "requests_per_minute": self._config.requests_per_minute,
            "request_timeout_seconds": self._config.request_timeout_seconds,
        }


def resolve_runtime_config(
    kind: str | None,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    allow_fallback: bool = False,
    locale: str | None = None,
    environment: dict[str, str] | None = None,
    env_file: dict[str, str] | None = None,
) -> LLMRuntimeConfig:
    """Resolve one runtime config with CLI > environment > ``.env`` priority."""
    shell = environment if environment is not None else dict(os.environ)
    file_values = env_file or {}

    def pick(
        flag: str | None, keys: tuple[str, ...], default: str | None = None
    ) -> str | None:
        if flag:
            return flag
        for key in keys:
            if shell.get(key):
                return shell[key]
        for key in keys:
            if file_values.get(key):
                return file_values[key]
        return default

    def parse_setting(
        keys: tuple[str, ...], default: int | float, parser: type[int] | type[float]
    ) -> int | float:
        value = pick(None, keys)
        if value is None:
            return default
        try:
            return parser(value)
        except ValueError as exc:
            joined = "/".join(keys)
            raise ValueError(f"{joined} must be numeric") from exc

    resolved = (pick(kind, ("BOOK2SKILL_LLM",), "mock") or "mock").lower()
    resolved_locale = resolve_locale(locale, environment=shell, env_file=file_values)
    if resolved == "mock":
        return LLMRuntimeConfig(locale=resolved_locale)
    if resolved not in {"openai", "compatible"}:
        raise ValueError(
            f"Unknown LLM adapter '{resolved}'. Expected 'mock', 'openai', "
            "or 'compatible'."
        )
    return LLMRuntimeConfig(
        provider="openai",
        model=pick(model, ("LLM_MODEL", "OPENAI_MODEL"), "gpt-4o") or "gpt-4o",
        base_url=pick(base_url, ("LLM_BASE_URL", "OPENAI_BASE_URL")),
        api_key=pick(api_key, ("LLM_API_KEY", "OPENAI_API_KEY")),
        allow_fallback=allow_fallback,
        locale=resolved_locale,
        max_concurrent_requests=int(
            parse_setting(
                ("BOOK2SKILL_LLM_MAX_CONCURRENT_REQUESTS",), 2, int
            )
        ),
        requests_per_minute=int(
            parse_setting(("BOOK2SKILL_LLM_REQUESTS_PER_MINUTE",), 15, int)
        ),
        request_timeout_seconds=float(
            parse_setting(
                ("BOOK2SKILL_LLM_REQUEST_TIMEOUT_SECONDS",), 30.0, float
            )
        ),
    )


def build_llm_adapter(
    config: LLMRuntimeConfig,
    *,
    cache_root: Path | None = None,
    timing_history_path: Path | None = None,
) -> RuntimeLLMAdapter:
    """Construct the only production adapter path from a resolved config."""
    return RuntimeLLMAdapter(
        config,
        cache_root=cache_root,
        timing_history_path=timing_history_path,
    )


def default_timing_history_path(
    environment: dict[str, str] | None = None,
) -> Path:
    """Return a platform-local path for redacted performance observations."""
    shell = environment if environment is not None else dict(os.environ)
    configured = shell.get("BOOK2SKILL_CACHE_HOME")
    if configured:
        return Path(configured).expanduser() / "performance-history.json"
    if shell.get("LOCALAPPDATA"):
        root = Path(shell["LOCALAPPDATA"])
    elif shell.get("XDG_CACHE_HOME"):
        root = Path(shell["XDG_CACHE_HOME"])
    else:
        root = Path.home() / ".cache"
    return root / "book2skill" / "performance-history.json"


class _ChunkAdapter(Protocol):
    """Private protocol shape shared by built-in providers only."""

    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        ...


class _ReviewAdapter(Protocol):
    """Private protocol for the optional Critic/Arbiter review capability."""

    def review_evidence(self, card: dict[str, object]) -> dict[str, object]:
        ...


def _chunk_input_tokens(items: list[ChunkItem]) -> int:
    """Estimate the complete prompt input carried by one analysis chunk."""
    return sum(
        estimate_tokens(item.text)
        + estimate_tokens(item.context_before)
        + estimate_tokens(item.context_after)
        for item in items
    )


__all__ = [
    "AnalysisRunManifest",
    "LLMInvocation",
    "LLMResponseError",
    "LLMRuntimeConfig",
    "LLMRuntimeError",
    "LLMUnavailableError",
    "RuntimeLLMAdapter",
    "build_llm_adapter",
    "default_timing_history_path",
    "resolve_runtime_config",
]
