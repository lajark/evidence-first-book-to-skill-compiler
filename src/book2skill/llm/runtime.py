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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from book2skill.config import Locale, resolve_locale
from book2skill.domain import TextBlock
from book2skill.llm.chunking import ChunkItem
from book2skill.llm.ports import LLMAdapter

Provider = Literal["mock", "openai"]
InvocationOutcome = Literal["success", "fallback", "error"]


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
    prompt_version: str = "analysis-v1"
    response_schema_version: str = "analysis-response-v1"
    locale: Locale = "zh-CN"
    max_retries: int = 2
    circuit_failure_threshold: int = 3
    max_concurrent_requests: int = 4
    requests_per_minute: int = 60
    request_timeout_seconds: float = 30.0

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

    operation: Literal["structure", "candidates", "skills", "chunk"]
    outcome: InvocationOutcome
    provider: Provider
    model: str
    prompt_version: str
    response_schema_version: str
    parameters: dict[str, float | int | str | bool]
    reason: str | None = None
    response_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    response_items: int | None = Field(default=None, ge=0)


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


class RuntimeLLMAdapter:
    """Wrap a provider with explicit fallback policy and redacted auditing."""

    def __init__(
        self, config: LLMRuntimeConfig, *, cache_root: Path | None = None
    ) -> None:
        self._config = config
        self._invocations: list[LLMInvocation] = []
        self._consecutive_failures = 0
        self._cache_root = cache_root
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
        )

    def start_run(self) -> None:
        """Clear prior invocation records before an independent Analyze run."""
        with self._state_lock:
            self._consecutive_failures = 0
        with self._invocation_lock:
            self._invocations.clear()

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

    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem], *, extractor_id: str = "unknown"
    ) -> dict[str, list[dict[str, object]]]:
        """Run one merged chunk request under the same fallback policy."""
        cache_key = self._cache_key(source_id, items, extractor_id)
        cached = self._load_cached_chunk(cache_key)
        if cached is not None:
            self._record("chunk", "success", cached, "cache_hit")
            return cached
        with self._state_lock:
            circuit_open = (
                self._consecutive_failures
                >= self._config.circuit_failure_threshold
            )
        if circuit_open:
            error = LLMRuntimeError("LLM circuit is open after consecutive failures")
            self._record("chunk", "error", None, str(error))
            raise error
        try:
            result = self._retry_chunk(source_id, items)
        except LLMRuntimeError as exc:
            with self._state_lock:
                self._consecutive_failures += 1
            if self._config.provider == "openai" and self._config.allow_fallback:
                result = self._chunk_dispatch(self._fallback, source_id, items)
                self._record("chunk", "fallback", result, str(exc))
                return result
            self._record("chunk", "error", None, str(exc))
            raise
        with self._state_lock:
            self._consecutive_failures = 0
        self._store_cached_chunk(cache_key, result)
        self._record("chunk", "success", result, None)
        return result

    def analyze_chunks(
        self, requests: list[tuple[str, list[ChunkItem], str]]
    ) -> list[dict[str, list[dict[str, object]]]]:
        """Run deterministic chunk requests with bounded real-provider concurrency.

        Results retain input order even when requests finish out of order. The
        offline Mock is deliberately kept sequential: it is CPU-local and that
        preserves its audit ordering without sacrificing network throughput.
        """
        if not requests:
            return []
        if self._config.provider == "mock" or len(requests) == 1:
            return [
                self.analyze_chunk(source_id, items, extractor_id=extractor_id)
                for source_id, items, extractor_id in requests
            ]

        results: list[dict[str, list[dict[str, object]]] | None] = [None] * len(
            requests
        )
        with ThreadPoolExecutor(
            max_workers=min(self._config.max_concurrent_requests, len(requests))
        ) as pool:
            futures = {
                pool.submit(
                    self.analyze_chunk,
                    source_id,
                    items,
                    extractor_id=extractor_id,
                ): index
                for index, (source_id, items, extractor_id) in enumerate(requests)
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()
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
            "items": [
                (item.input_id, item.source_block_id, item.text) for item in items
            ],
        }
        return hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

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

    def _retry_chunk(
        self, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        last_error: LLMRuntimeError | None = None
        for _ in range(self._config.max_retries + 1):
            try:
                self._await_request_slot()
                return self._chunk_dispatch(self._provider, source_id, items)
            except LLMRuntimeError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def _await_request_slot(self) -> None:
        """Serialize real-provider request starts to honour the configured rate."""
        if self._config.provider == "mock":
            return
        interval = 60.0 / self._config.requests_per_minute
        with self._rate_lock:
            now = time.monotonic()
            scheduled = max(now, self._next_request_start)
            self._next_request_start = scheduled + interval
        delay = scheduled - now
        if delay > 0:
            time.sleep(delay)

    def _invoke(
        self,
        operation: Literal["structure", "candidates", "skills"],
        source_id: str,
        payload: list[TextBlock] | list[dict[str, object]],
    ) -> list[dict[str, object]]:
        try:
            result = self._dispatch(self._provider, operation, source_id, payload)
        except LLMRuntimeError as exc:
            if self._config.provider == "openai" and self._config.allow_fallback:
                result = self._dispatch(self._fallback, operation, source_id, payload)
                self._record(operation, "fallback", result, str(exc))
                return result
            self._record(operation, "error", None, str(exc))
            raise
        self._record(operation, "success", result, None)
        return result

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

    def _record(
        self,
        operation: Literal["structure", "candidates", "skills", "chunk"],
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
                ("BOOK2SKILL_LLM_MAX_CONCURRENT_REQUESTS",), 4, int
            )
        ),
        requests_per_minute=int(
            parse_setting(("BOOK2SKILL_LLM_REQUESTS_PER_MINUTE",), 60, int)
        ),
        request_timeout_seconds=float(
            parse_setting(
                ("BOOK2SKILL_LLM_REQUEST_TIMEOUT_SECONDS",), 30.0, float
            )
        ),
    )


def build_llm_adapter(
    config: LLMRuntimeConfig, *, cache_root: Path | None = None
) -> RuntimeLLMAdapter:
    """Construct the only production adapter path from a resolved config."""
    return RuntimeLLMAdapter(config, cache_root=cache_root)


class _ChunkAdapter(Protocol):
    """Private protocol shape shared by built-in providers only."""

    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem]
    ) -> dict[str, list[dict[str, object]]]:
        ...


__all__ = [
    "AnalysisRunManifest",
    "LLMInvocation",
    "LLMResponseError",
    "LLMRuntimeConfig",
    "LLMRuntimeError",
    "LLMUnavailableError",
    "RuntimeLLMAdapter",
    "build_llm_adapter",
    "resolve_runtime_config",
]
