"""Multi-channel ``balanced`` routing for the LLM pipeline (OPT-P1-09).

A :class:`RouterLLMAdapter` fans Map work out across several provider
profiles so independent cloud quotas can be used in parallel, while Reduce /
Synthesis / Skill requests are routed to the configured strong model. It
implements the same protocol as :class:`RuntimeLLMAdapter` (minus
single-channel specifics), so the application layer needs no changes.

Routing contract
----------------
* Each Map chunk is routed to exactly one eligible channel
  (:meth:`ProviderProfile.can_serve` + :meth:`ProviderProfile.accepts_material`).
* Map selection is least-loaded-first so parallel channels use their aggregate
  concurrency/RPM; Reduce / Synthesis / Skill prefer the configured strong model.
* Global concurrency is bounded by a hard cap so parallel channels cannot
  spend unboundedly; per-channel RPM / retries / circuit breaking are handled
  by the channel's own :class:`RuntimeLLMAdapter`.
* Results are returned in input order regardless of completion order.
* A failing channel is retried once on another eligible channel; if no
  channel remains the run fails closed without advancing completion.
* Every selection is recorded as a redacted :class:`RoutingDecision`
  (profile_id, role, strategy version, reason, candidate snapshot).
* No credential, prompt, endpoint secret or source text is ever recorded.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from book2skill.config import Locale
from book2skill.domain import TextBlock
from book2skill.llm.chunking import ChunkItem
from book2skill.llm.profiles import (
    MaterialCategory,
    ProviderProfile,
    ProviderProfileSet,
    Role,
    profile_to_runtime_config,
)
from book2skill.llm.runtime import (
    AnalysisRunManifest,
    ChunkProgressEvent,
    LLMInvocation,
    LLMRuntimeConfig,
    LLMRuntimeError,
    RuntimeLLMAdapter,
)

# Routing strategy version for the balanced policy. It is carried into every
# channel config so the content cache is namespaced per strategy and never
# reuses results from a different routing policy.
#
# v3 makes Map selection pure least-in-flight-first so parallel channels use
# their aggregate concurrency/RPM instead of converging on one channel.
BALANCED_STRATEGY_VERSION = "balanced-v3"

# Default hard budget caps (overridable via env). The global worker cap stops
# parallel channels from spending unboundedly even when the sum of per-profile
# maxima is large.
_DEFAULT_GLOBAL_WORKERS = 8
_DEFAULT_MAX_TOTAL_CALLS = 2000
_DEFAULT_MAX_TOTAL_COST = 50.0

# Roles that should prefer the configured strong model (default_profile).
_STRONG_MODEL_ROLES = frozenset(
    {"section_reduce", "book_reduce", "synthesis", "skill"}
)


@dataclass
class ChannelHealth:
    """Rolling health state for one provider channel."""

    profile_id: str
    ewma_latency: float = 0.0
    in_flight: int = 0
    consecutive_failures: int = 0
    total_calls: int = 0
    total_cost: float = 0.0

    @property
    def circuit_open(self) -> bool:
        return self.consecutive_failures >= 3


@dataclass(frozen=True)
class RoutingDecision:
    """A redacted, auditable record of one channel selection."""

    profile_id: str
    role: Role
    strategy_version: str
    reason: str
    candidates: tuple[str, ...] = field(default=())


class ChannelPool:
    """Resolve a profile set into routed channel adapters plus health state."""

    def __init__(
        self,
        profile_set: ProviderProfileSet,
        *,
        allow_fallback: bool = False,
        locale: Locale = "zh-CN",
        cache_root: Path | None = None,
        env_file: dict[str, str] | None = None,
    ) -> None:
        self._profiles = {p.profile_id: p for p in profile_set.profiles}
        self._default_profile = profile_set.default_profile
        self._channels: dict[str, RuntimeLLMAdapter] = {}
        self._health: dict[str, ChannelHealth] = {}
        for profile in profile_set.profiles:
            config = dataclasses_replace_strategy(
                profile_to_runtime_config(
                    profile,
                    allow_fallback=allow_fallback,
                    locale=locale,
                    env_file=env_file,
                ),
                profile.profile_id,
            )
            self._channels[profile.profile_id] = RuntimeLLMAdapter(
                config, cache_root=cache_root
            )
            self._health[profile.profile_id] = ChannelHealth(
                profile_id=profile.profile_id,
                ewma_latency=(
                    profile.request_timeout_seconds / 2.0
                    if profile.provider == "openai"
                    else 0.0
                ),
            )

    @property
    def default_profile(self) -> str:
        return self._default_profile

    def profile(self, profile_id: str) -> ProviderProfile:
        return self._profiles[profile_id]

    def channel(self, profile_id: str) -> RuntimeLLMAdapter:
        return self._channels[profile_id]

    def health(self, profile_id: str) -> ChannelHealth:
        return self._health[profile_id]

    def profiles(self) -> list[ProviderProfile]:
        return list(self._profiles.values())

    def mark_success(
        self, profile_id: str, latency_seconds: float, cost: float = 0.0
    ) -> None:
        health = self._health[profile_id]
        alpha = 0.3
        health.ewma_latency = (
            alpha * latency_seconds + (1.0 - alpha) * health.ewma_latency
        )
        health.consecutive_failures = 0
        health.in_flight = max(0, health.in_flight - 1)
        health.total_calls += 1
        health.total_cost += cost

    def mark_failure(self, profile_id: str) -> None:
        health = self._health[profile_id]
        health.consecutive_failures += 1
        health.in_flight = max(0, health.in_flight - 1)

    def mark_in_flight(self, profile_id: str) -> None:
        self._health[profile_id].in_flight += 1


def dataclasses_replace_strategy(
    config: LLMRuntimeConfig, profile_id: str
) -> LLMRuntimeConfig:
    """Carry the balanced strategy version and profile id into the channel config."""
    from dataclasses import replace

    return replace(
        config,
        routing_strategy_version=BALANCED_STRATEGY_VERSION,
        profile_id=profile_id,
    )


class RouterLLMAdapter:
    """A ``balanced`` multi-channel adapter over the shared LLM protocol."""

    def __init__(
        self,
        profile_set: ProviderProfileSet,
        *,
        allow_fallback: bool = False,
        locale: Locale = "zh-CN",
        data_home: os.PathLike[str] | None = None,
        material_category: MaterialCategory = "licensed_book",
        max_global_workers: int | None = None,
        max_total_calls: int | None = None,
        max_total_cost: float | None = None,
        env_file: dict[str, str] | None = None,
    ) -> None:
        cache_root = (
            Path(data_home) / ".cache" / "llm" if data_home is not None else None
        )
        self._pool = ChannelPool(
            profile_set,
            allow_fallback=allow_fallback,
            locale=locale,
            cache_root=cache_root,
            env_file=env_file,
        )
        self._material_category = material_category
        shell = dict(os.environ)
        self._max_global_workers = max_global_workers or min(
            sum(p.max_concurrent_requests for p in profile_set.profiles),
            _DEFAULT_GLOBAL_WORKERS,
        )
        self._max_total_calls = max_total_calls or int(
            shell.get("BOOK2SKILL_LLM_MAX_TOTAL_CALLS", _DEFAULT_MAX_TOTAL_CALLS)
        )
        self._max_total_cost = max_total_cost or float(
            shell.get("BOOK2SKILL_LLM_MAX_TOTAL_COST", _DEFAULT_MAX_TOTAL_COST)
        )
        self._lock = threading.Lock()
        self._total_calls = 0
        self._total_cost = 0.0
        self._last_decisions: list[RoutingDecision] = []

    @property
    def config(self) -> LLMRuntimeConfig:
        """Return the default profile's config (for Build/Update runtime_config)."""
        return self._pool.channel(self._pool.default_profile).config

    def start_run(self) -> None:
        """Start a fresh routing run (clears prior decision records)."""
        with self._lock:
            self._last_decisions = []

    def estimate_chunk_eta(self, items: list[ChunkItem]) -> None:
        """Multi-channel routing has no single-channel ETA; return None."""
        return None

    def estimate_input_eta(
        self, input_tokens: int, *, operation: str = "chunk"
    ) -> None:
        """Multi-channel routing has no single-channel ETA; return None."""
        return None

    @property
    def manifest(self) -> AnalysisRunManifest:
        """Aggregate per-channel manifests into one redacted run manifest."""
        primary = self._pool.channel(self._pool.default_profile).manifest

        invocations: list[LLMInvocation] = []
        for profile_id in self._pool.profiles():
            channel = self._pool.channel(profile_id.profile_id)
            invocations.extend(channel.manifest.invocations)
        return AnalysisRunManifest(
            provider=primary.provider,
            model=primary.model,
            prompt_version=primary.prompt_version,
            response_schema_version=primary.response_schema_version,
            locale=primary.locale,
            parameters=primary.parameters,
            fallback_allowed=primary.fallback_allowed,
            invocations=invocations,
            profile_id=primary.profile_id,
            data_send_policy=primary.data_send_policy,
        )

    @property
    def decisions(self) -> list[RoutingDecision]:
        """Last run's routing decisions (input order) for audit and tests."""
        return list(self._last_decisions)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _eligible(self, role: Role) -> list[ProviderProfile]:
        return [
            p
            for p in self._pool.profiles()
            if p.can_serve(role) and p.accepts_material(self._material_category)
        ]

    def _score(self, profile: ProviderProfile, role: Role) -> float:
        health = self._pool.health(profile.profile_id)
        if health.circuit_open:
            return 1e9
        load = health.in_flight / max(1, profile.max_concurrent_requests)
        latency = health.ewma_latency or 1.0
        cost = (
            (profile.cost_per_million_input_tokens or 0.0)
            / max(1.0, profile.cost_per_million_input_tokens or 1.0)
            if profile.cost_per_million_input_tokens
            else 0.0
        )
        # Strong-model roles strongly prefer the default profile regardless of
        # latency/load so Reduce / Synthesis use the configured strong model.
        strong_bonus = (
            -5.0
            if (
                role in _STRONG_MODEL_ROLES
                and profile.profile_id == self._pool.default_profile
            )
            else 0.0
        )
        return latency + load + cost + strong_bonus

    def _map_sort_key(self, profile: ProviderProfile) -> tuple[bool, float, float, str]:
        """Pure least-in-flight ordering for Map work.

        Ordered by (circuit-open, in-flight load, latency, profile id): a
        circuit-broken channel sorts last; among healthy channels the least
        loaded wins so parallel channels use their aggregate concurrency/RPM.
        Latency only breaks ties between equally loaded channels, so a
        genuinely slow channel still receives work when it is the least loaded
        (the circuit breaker handles a truly dead channel).
        """
        health = self._pool.health(profile.profile_id)
        return (
            health.circuit_open,
            health.in_flight / max(1, profile.max_concurrent_requests),
            health.ewma_latency or 1.0,
            profile.profile_id,
        )

    def select_channel(self, role: Role) -> RoutingDecision:
        eligible = self._eligible(role)
        if not eligible:
            raise LLMRuntimeError(
                f"No provider profile can serve role '{role}' for material "
                f"'{self._material_category}'"
            )
        if role == "map":
            best = min(eligible, key=self._map_sort_key)
        else:
            best = min(
                eligible,
                key=lambda p: (self._score(p, role), p.profile_id),
            )
        decision = RoutingDecision(
            profile_id=best.profile_id,
            role=role,
            strategy_version=BALANCED_STRATEGY_VERSION,
            reason="lowest_health_score",
            candidates=tuple(
                p.profile_id for p in sorted(eligible, key=lambda p: p.profile_id)
            ),
        )
        with self._lock:
            self._last_decisions.append(decision)
        return decision

    def _failover_order(self, decision: RoutingDecision) -> list[str]:
        return [decision.profile_id] + [
            pid for pid in decision.candidates if pid != decision.profile_id
        ]

    def _within_budget(self, profile_id: str, est_cost: float) -> bool:
        with self._lock:
            if self._total_calls >= self._max_total_calls:
                return False
            if self._total_cost + est_cost > self._max_total_cost:
                return False
            self._total_calls += 1
            self._total_cost += est_cost
            return True

    # ------------------------------------------------------------------
    # Shared protocol
    # ------------------------------------------------------------------

    def analyze_chunk(
        self, source_id: str, items: list[ChunkItem], *, extractor_id: str = "unknown"
    ) -> dict[str, list[dict[str, object]]]:
        decision = self.select_channel("map")
        last_error: LLMRuntimeError | None = None
        for profile_id in self._failover_order(decision):
            est_cost = self._estimate_cost(profile_id, items)
            if not self._within_budget(profile_id, est_cost):
                raise LLMRuntimeError(
                    "global LLM budget exceeded (calls or estimated cost)"
                )
            channel = self._pool.channel(profile_id)
            self._pool.mark_in_flight(profile_id)
            started = time.monotonic()
            try:
                result = channel.analyze_chunk(
                    source_id, items, extractor_id=extractor_id
                )
                self._pool.mark_success(
                    profile_id, time.monotonic() - started, cost=est_cost
                )
                return result
            except LLMRuntimeError as exc:
                last_error = exc
                self._pool.mark_failure(profile_id)
                continue
        assert last_error is not None
        raise last_error

    def analyze_chunks(
        self,
        requests: list[tuple[str, list[ChunkItem], str]],
        *,
        on_completed: Callable[[int, int, int], None] | None = None,
        on_status: Callable[[ChunkProgressEvent], None] | None = None,
    ) -> list[dict[str, list[dict[str, object]]]]:
        total = len(requests)
        results: list[dict[str, list[dict[str, object]]] | None] = [None] * total
        completed = 0
        lock = threading.Lock()
        decisions: list[RoutingDecision | None] = [None] * total

        def run(
            index: int, source_id: str, items: list[ChunkItem], extractor_id: str
        ) -> None:
            nonlocal completed
            decision = self.select_channel("map")
            decisions[index] = decision
            last_error: LLMRuntimeError | None = None
            for profile_id in self._failover_order(decision):
                est_cost = self._estimate_cost(profile_id, items)
                if not self._within_budget(profile_id, est_cost):
                    raise LLMRuntimeError(
                        "global LLM budget exceeded (calls or estimated cost)"
                    )
                channel = self._pool.channel(profile_id)
                self._pool.mark_in_flight(profile_id)
                started = time.monotonic()
                try:
                    result = channel.analyze_chunk(
                        source_id, items, extractor_id=extractor_id
                    )
                    self._pool.mark_success(
                        profile_id, time.monotonic() - started, cost=est_cost
                    )
                    with lock:
                        results[index] = result
                        completed += 1
                    if on_completed is not None:
                        on_completed(completed, total, index)
                    return
                except LLMRuntimeError as exc:
                    last_error = exc
                    self._pool.mark_failure(profile_id)
                    continue
            if last_error is not None:
                raise last_error

        with ThreadPoolExecutor(max_workers=self._max_global_workers) as pool:
            futures = [
                pool.submit(run, index, source_id, items, extractor_id)
                for index, (source_id, items, extractor_id) in enumerate(requests)
            ]
            for future in as_completed(futures):
                future.result()  # propagate budget / all-channels-failed errors
        with lock:
            self._last_decisions = [d for d in decisions if d is not None]
        return [r if r is not None else {} for r in results]

    def analyze_structure(
        self, source_id: str, blocks: list[TextBlock]
    ) -> list[dict[str, object]]:
        decision = self.select_channel("map")
        return self._pool.channel(decision.profile_id).analyze_structure(
            source_id, blocks
        )

    def extract_candidates(
        self, source_id: str, blocks: list[TextBlock]
    ) -> list[dict[str, object]]:
        decision = self.select_channel("map")
        return self._pool.channel(decision.profile_id).extract_candidates(
            source_id, blocks
        )

    def suggest_skills(
        self, source_id: str, candidates: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        decision = self.select_channel("skill")
        return self._pool.channel(decision.profile_id).suggest_skills(
            source_id, candidates
        )

    def suggest_skills_with_progress(
        self,
        source_id: str,
        candidates: list[dict[str, object]],
        *,
        on_status: Callable[[ChunkProgressEvent], None] | None = None,
    ) -> list[dict[str, object]]:
        return self.suggest_skills(source_id, candidates)

    def synthesize_candidates(
        self,
        source_id: str,
        level: Literal["chapter", "book"],
        candidates: list[dict[str, object]],
        structure: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        role: Role = "section_reduce" if level == "chapter" else "book_reduce"
        decision = self.select_channel(role)
        return self._pool.channel(decision.profile_id).synthesize_candidates(
            source_id, level, candidates, structure
        )

    def _estimate_cost(self, profile_id: str, items: list[ChunkItem]) -> float:
        from book2skill.llm.chunking import estimate_tokens

        profile = self._pool.profile(profile_id)
        cost_per_million = profile.cost_per_million_input_tokens
        if cost_per_million is None:
            return 0.0
        tokens = estimate_tokens(" ".join(item.text for item in items))
        return tokens / 1_000_000.0 * cost_per_million
