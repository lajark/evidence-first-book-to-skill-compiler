"""Routing, failover, budget and cache-isolation tests for OPT-P1-09."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
import yaml

from book2skill.domain.models import Locator, LocatorKind
from book2skill.llm.chunking import ChunkItem
from book2skill.llm.profiles import ProviderProfile, ProviderProfileSet
from book2skill.llm.router import (
    BALANCED_STRATEGY_VERSION,
    ChannelPool,
    RouterLLMAdapter,
)
from book2skill.llm.runtime import LLMRuntimeError


def _mock_profile(profile_id: str, **overrides: object) -> ProviderProfile:
    base: dict[str, object] = {
        "profile_id": profile_id,
        "provider": "mock",
        "model": "mock-rule-based-v1",
        "api_key_env": "MOCK_PLACEHOLDER",
        "roles": ["map", "section_reduce", "book_reduce", "synthesis", "skill"],
    }
    base.update(overrides)
    return ProviderProfile.model_validate(base)


def _real_profile(profile_id: str, key_env: str) -> ProviderProfile:
    return ProviderProfile.model_validate(
        {
            "profile_id": profile_id,
            "provider": "openai",
            "model": "deepseek-v4-flash-0731",
            "base_url": "https://example.invalid/v1",
            "api_key_env": key_env,
            "roles": ["map"],
        }
    )


def _two_profile_set(
    a: ProviderProfile | None = None, b: ProviderProfile | None = None
) -> ProviderProfileSet:
    left = a or _mock_profile("map-a")
    right = b or _mock_profile("map-b")
    return ProviderProfileSet(profiles=[left, right], default_profile="map-a")


def _chunk(text: str = "hello") -> list[ChunkItem]:
    return [
        ChunkItem(
            input_id="b1",
            source_block_id="b1",
            text=text,
            locator=Locator(kind=LocatorKind.UNKNOWN),
        )
    ]


class TestEnvFileCredential:
    def test_channel_credential_from_env_file(self, monkeypatch) -> None:
        monkeypatch.delenv("ALPHA_KEY", raising=False)
        profile_set = ProviderProfileSet(
            profiles=[_real_profile("alpha", "ALPHA_KEY")],
            default_profile="alpha",
        )
        pool = ChannelPool(profile_set, env_file={"ALPHA_KEY": "sk-envfile"})
        assert pool.channel("alpha").config.api_key == "sk-envfile"

    def test_shell_env_wins_over_env_file(self, monkeypatch) -> None:
        monkeypatch.setenv("ALPHA_KEY", "sk-shell")
        profile_set = ProviderProfileSet(
            profiles=[_real_profile("alpha", "ALPHA_KEY")],
            default_profile="alpha",
        )
        pool = ChannelPool(profile_set, env_file={"ALPHA_KEY": "sk-envfile"})
        assert pool.channel("alpha").config.api_key == "sk-shell"


class TestRoutingSelection:
    def test_map_chunks_route_to_eligible_mock_channel(self, tmp_path: Path) -> None:
        router = RouterLLMAdapter(_two_profile_set(), data_home=tmp_path)
        result = router.analyze_chunks(
            [("src", _chunk(), "ext"), ("src", _chunk("two"), "ext")]
        )
        assert len(result) == 2
        assert all(router.decisions)
        # Every chunk is routed to an eligible channel; load-aware routing may
        # spread across both map-a and map-b once in-flight rises.
        assert {d.profile_id for d in router.decisions} <= {"map-a", "map-b"}
        assert all(
            d.strategy_version == BALANCED_STRATEGY_VERSION
            for d in router.decisions
        )

    def test_map_spreads_even_with_large_latency_gap(self, tmp_path: Path) -> None:
        # Pure least-in-flight: even a 100x slower channel receives Map work
        # when it is the least loaded, so aggregate concurrency is used. A
        # truly dead channel is excluded by the circuit breaker, not latency.
        router = RouterLLMAdapter(_two_profile_set(), data_home=tmp_path)
        router._pool.health("map-a").ewma_latency = 100.0
        router._pool.health("map-b").ewma_latency = 1.0
        router.analyze_chunks([("src", _chunk(), "ext") for _ in range(3)])
        used = {d.profile_id for d in router.decisions}
        assert used == {"map-a", "map-b"}

    def test_map_load_balances_across_channels_despite_modest_latency(
        self, tmp_path: Path
    ) -> None:
        """A modest latency gap must not collapse Map onto one channel."""
        router = RouterLLMAdapter(_two_profile_set(), data_home=tmp_path)
        # map-a is modestly slower than map-b; least-loaded-first still spreads
        # chunks so both channels' aggregate concurrency is actually used.
        router._pool.health("map-a").ewma_latency = 3.0
        router._pool.health("map-b").ewma_latency = 1.0
        router.analyze_chunks([("src", _chunk(str(i)), "ext") for i in range(8)])
        used = {d.profile_id for d in router.decisions}
        assert used == {"map-a", "map-b"}

    def test_role_eligible_only(self, tmp_path: Path) -> None:
        profile_set = _two_profile_set(
            a=_mock_profile("map-a", roles=["map"]),
            b=_mock_profile("map-b", roles=["synthesis"]),
        )
        router = RouterLLMAdapter(profile_set, data_home=tmp_path)
        # Only map-a can serve 'map'; synthesis falls back to map-b (only one).
        assert router.select_channel("map").profile_id == "map-a"
        assert router.select_channel("synthesis").profile_id == "map-b"

    def test_material_policy_fail_closed(self, tmp_path: Path) -> None:
        profile_set = _two_profile_set(
            a=_mock_profile("map-a", roles=["map"]),
            b=_mock_profile(
                "map-b",
                roles=["map"],
                allowed_material_categories=["public_doc"],
            ),
        )
        router = RouterLLMAdapter(
            profile_set, data_home=tmp_path, material_category="licensed_book"
        )
        # map-b rejects licensed_book; only map-a is eligible.
        assert router.select_channel("map").profile_id == "map-a"

    def test_no_eligible_channel_fails_closed(self, tmp_path: Path) -> None:
        profile_set = _two_profile_set(
            a=_mock_profile("map-a", roles=["synthesis"]),
            b=_mock_profile("map-b", roles=["synthesis"]),
        )
        router = RouterLLMAdapter(profile_set, data_home=tmp_path)
        with pytest.raises(LLMRuntimeError, match="No provider profile"):
            router.select_channel("map")


class TestFailover:
    def test_channel_failure_fails_over_to_second(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = RouterLLMAdapter(_two_profile_set(), data_home=tmp_path)
        failing = router._pool.channel("map-a")

        def boom(*_args, **_kwargs):
            raise LLMRuntimeError("simulated 429")

        monkeypatch.setattr(failing, "analyze_chunk", boom)
        result = router.analyze_chunks([("src", _chunk(), "ext")])
        assert result[0]  # result present from the healthy channel
        assert router._pool.health("map-a").consecutive_failures == 1
        assert router._pool.health("map-b").total_calls == 1

    def test_all_channels_fail_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = RouterLLMAdapter(_two_profile_set(), data_home=tmp_path)
        for pid in ("map-a", "map-b"):
            monkeypatch.setattr(
                router._pool.channel(pid),
                "analyze_chunk",
                lambda *_a, **_k: (_ for _ in ()).throw(
                    LLMRuntimeError("simulated timeout")
                ),
            )
        with pytest.raises(LLMRuntimeError):
            router.analyze_chunks([("src", _chunk(), "ext")])


class TestBudget:
    def test_max_total_calls_enforced(self, tmp_path: Path) -> None:
        router = RouterLLMAdapter(
            _two_profile_set(), data_home=tmp_path, max_total_calls=1
        )
        with pytest.raises(LLMRuntimeError, match="budget"):
            router.analyze_chunks(
                [("src", _chunk(), "ext"), ("src", _chunk("two"), "ext")]
            )

    def test_global_worker_cap_is_bounded(self, tmp_path: Path) -> None:
        router = RouterLLMAdapter(_two_profile_set(), data_home=tmp_path)
        # Default cap = min(sum of per-profile max_concurrent (2+2), hard cap 8).
        assert router._max_global_workers == 4

    def test_concurrency_never_exceeds_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = RouterLLMAdapter(
            _two_profile_set(), data_home=tmp_path, max_global_workers=1
        )
        active = 0
        max_seen = 0
        lock = threading.Lock()

        def tracking(channel):
            def analyze(*_args, **_kwargs):
                nonlocal active, max_seen
                with lock:
                    active += 1
                    max_seen = max(max_seen, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return {"structure": [], "candidates": []}

            return analyze

        for pid in ("map-a", "map-b"):
            monkeypatch.setattr(
                router._pool.channel(pid), "analyze_chunk", tracking(pid)
            )
        result = router.analyze_chunks(
            [("src", _chunk(), "ext") for _ in range(4)]
        )
        assert len(result) == 4
        assert max_seen == 1


class TestCacheIsolation:
    def test_router_channels_use_balanced_strategy_in_cache(
        self, tmp_path: Path
    ) -> None:
        router = RouterLLMAdapter(_two_profile_set(), data_home=tmp_path)
        for pid in ("map-a", "map-b"):
            config = router._pool.channel(pid).config
            assert config.routing_strategy_version == BALANCED_STRATEGY_VERSION
            assert config.profile_id == pid

    def test_different_strategy_does_not_reuse_content_cache(
        self, tmp_path: Path
    ) -> None:
        router = RouterLLMAdapter(_two_profile_set(), data_home=tmp_path)
        channel = router._pool.channel("map-a")
        key = channel._cache_key("src", _chunk(), "ext")
        # A single-channel run on the same profile but a different strategy
        # version must produce a different cache key.
        from book2skill.llm.runtime import LLMRuntimeConfig, RuntimeLLMAdapter

        single = RuntimeLLMAdapter(
            LLMRuntimeConfig(
                provider="mock",
                profile_id="map-a",
                routing_strategy_version="single-v1",
            )
        )
        assert key != single._cache_key("src", _chunk(), "ext")


class TestCliBalanced:
    def test_cli_balanced_strategy_emits_valid_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from typer.testing import CliRunner

        from book2skill.cli import app

        path = tmp_path / "profiles.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "default_profile": "map-a",
                    "profiles": [
                        {
                            "profile_id": "map-a",
                            "provider": "mock",
                            "model": "mock-rule-based-v1",
                            "api_key_env": "MOCK_PLACEHOLDER",
                            "roles": [
                                "map",
                                "section_reduce",
                                "book_reduce",
                                "synthesis",
                                "skill",
                            ],
                        },
                        {
                            "profile_id": "map-b",
                            "provider": "mock",
                            "model": "mock-rule-based-v1",
                            "api_key_env": "MOCK_PLACEHOLDER",
                            "roles": ["map"],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        src = tmp_path / "book.txt"
        src.write_text(
            "# Heading\nAlways validate input before processing.\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "analyze",
                str(src),
                "--llm-profiles",
                str(path),
                "--llm-strategy",
                "balanced",
                "--data-home",
                str(tmp_path / "data"),
                "--bundle-dir",
                str(tmp_path / "bundles"),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout
        envelope = json.loads(result.stdout)
        assert "error" not in envelope
        assert (envelope.get("analysis_run") or {}).get("profile_id") == "map-a"

    def test_balanced_without_profiles_rejected(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from book2skill.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "analyze",
                str(tmp_path / "x.txt"),
                "--llm-strategy",
                "balanced",
            ],
        )
        assert result.exit_code != 0
