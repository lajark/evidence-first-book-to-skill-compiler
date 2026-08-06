"""Security and parsing tests for multi-provider LLM profiles (OPT-P1-08)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from book2skill.domain.models import Locator, LocatorKind
from book2skill.llm.chunking import ChunkItem
from book2skill.llm.profiles import (
    ProviderProfile,
    ProviderProfileSet,
    load_profile_set,
    profile_set_from_env,
    profile_to_runtime_config,
    resolve_credentials,
)
from book2skill.llm.runtime import LLMRuntimeConfig, LLMUnavailableError


def _profile(**overrides: object) -> ProviderProfile:
    base: dict[str, object] = {
        "profile_id": "alpha",
        "provider": "openai",
        "model": "qwen-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "LLM_API_KEY",
        "roles": ["map"],
    }
    base.update(overrides)
    return ProviderProfile.model_validate(base)


def _profile_set(
    *profiles: ProviderProfile, default: str | None = None
) -> ProviderProfileSet:
    items = list(profiles) or [_profile()]
    return ProviderProfileSet(
        profiles=items,
        default_profile=default or items[0].profile_id,
    )


class TestProfileSetValidation:
    def test_two_profiles_independently_valid(self) -> None:
        a = _profile(profile_id="a")
        b = _profile(profile_id="b", model="gpt-4o", api_key_env="OPENAI_API_KEY")
        profiles = _profile_set(a, b, default="a")
        assert {p.profile_id for p in profiles.profiles} == {"a", "b"}
        assert profiles.profile("b").model == "gpt-4o"
        # default when no id given
        assert profiles.profile(None).profile_id == "a"

    def test_streaming_capability_threads_to_runtime_config(self) -> None:
        profile = _profile(streaming=True)
        config = profile_to_runtime_config(
            profile, env_file={"LLM_API_KEY": "test-key"}
        )
        assert config.streaming is True

    @pytest.mark.parametrize(
        "override,fragment",
        [
            ({"default_profile": "missing"}, "not declared"),
            ({"profiles": []}, ""),
        ],
    )
    def test_invalid_set_rejected(
        self, override: dict[str, object], fragment: str
    ) -> None:
        payload = {
            "schema_version": 1,
            "profiles": [
                {"profile_id": "a", "provider": "openai", "model": "m",
                 "api_key_env": "LLM_API_KEY", "roles": ["map"]},
            ],
            "default_profile": "a",
        }
        payload.update(override)
        with pytest.raises(ValidationError):
            ProviderProfileSet.model_validate(payload)

    def test_duplicate_profile_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            _profile_set(_profile(), _profile())

    def test_default_not_in_profiles_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not declared"):
            ProviderProfileSet(
                profiles=[_profile()], default_profile="nope"
            )

    @pytest.mark.parametrize(
        "override",
        [
            {"roles": []},
            {"max_concurrent_requests": 0},
            {"requests_per_minute": 0},
            {"request_timeout_seconds": 0},
            {"max_retries": -1},
            {"circuit_failure_threshold": 0},
        ],
    )
    def test_invalid_profile_field_rejected(self, override: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            _profile(**override)


class TestNoSecretsInConfig:
    def test_api_key_literal_field_rejected(self) -> None:
        # extra="forbid" rejects an accidental credential field.
        with pytest.raises(ValidationError):
            _profile(api_key="sk-leaked")  # type: ignore[call-arg]

    def test_api_key_env_must_be_env_var_name_not_credential(self) -> None:
        # A literal credential value is not a valid env-var name.
        with pytest.raises(ValidationError, match="environment variable"):
            _profile(api_key_env="sk-leaked-secret")

    @pytest.mark.parametrize("bad", ["Bad", "-leading", "trailing-", "under_score"])
    def test_invalid_profile_id_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            _profile(profile_id=bad)

    def test_model_dump_never_contains_credential(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-super-secret-value-xyz")
        profile = _profile()
        dumped = profile.model_dump()
        assert "api_key" not in dumped
        assert "sk-super-secret-value-xyz" not in json.dumps(dumped)
        # Runtime config keeps the key redacted in repr and never in model_dump
        config = profile_to_runtime_config(profile)
        assert "sk-super-secret-value-xyz" not in repr(config)
        assert config.api_key == "sk-super-secret-value-xyz"  # lives in-process only


class TestCredentialResolution:
    def test_reads_named_env_var_at_call_time(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-live")
        assert resolve_credentials(_profile()) == "sk-live"

    def test_missing_credential_fail_closed(self, monkeypatch) -> None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(LLMUnavailableError, match="LLM_API_KEY"):
            resolve_credentials(_profile())

    def test_env_file_fallback_when_env_var_missing(self, monkeypatch) -> None:
        # Shell env wins, .env used only when the process env is empty.
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        assert (
            resolve_credentials(_profile(), env_file={"LLM_API_KEY": "sk-envfile"})
            == "sk-envfile"
        )

    def test_env_var_takes_precedence_over_env_file(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-shell")
        assert resolve_credentials(
            _profile(), env_file={"LLM_API_KEY": "sk-envfile"}
        ) == "sk-shell"

    def test_env_file_fallback_reaches_runtime_config(self, monkeypatch) -> None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        config = profile_to_runtime_config(
            _profile(), env_file={"LLM_API_KEY": "sk-envfile"}
        )
        assert config.api_key == "sk-envfile"

    def test_manifest_records_profile_without_key(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-do-not-leak")
        from book2skill.llm.runtime import RuntimeLLMAdapter

        adapter = RuntimeLLMAdapter(profile_to_runtime_config(_profile()))
        manifest_json = adapter.manifest.model_dump_json()
        assert "sk-do-not-leak" not in manifest_json
        assert json.loads(manifest_json)["profile_id"] == "alpha"
        assert json.loads(manifest_json)["data_send_policy"] == "original_text_allowed"


class TestMaterialPolicy:
    def test_no_send_refuses_all_material(self) -> None:
        profile = _profile(data_send_policy="no_send")
        assert profile.accepts_material("licensed_book") is False
        assert profile.accepts_material("public_doc") is False

    def test_allowed_categories_enforced(self) -> None:
        profile = _profile(
            allowed_material_categories=["public_doc"]
        )
        assert profile.accepts_material("public_doc") is True
        assert profile.accepts_material("licensed_book") is False

    def test_unrestricted_accepts_all(self) -> None:
        profile = _profile()
        for cat in ("licensed_book", "internal_doc", "public_doc", "user_provided"):
            assert profile.accepts_material(cat) is True  # type: ignore[arg-type]

    def test_can_serve_role(self) -> None:
        assert _profile(roles=["map", "synthesis"]).can_serve("map") is True
        assert _profile(roles=["map", "synthesis"]).can_serve("arbiter") is False


class TestDynamicActive:
    def test_keeps_only_slots_with_credential(self, monkeypatch) -> None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        set_ = _profile_set(
            _profile(profile_id="alpha", api_key_env="ALPHA_KEY"),
            _profile(profile_id="beta", api_key_env="BETA_KEY"),
            default="alpha",
        )
        monkeypatch.setenv("ALPHA_KEY", "sk-a")
        active = set_.active()
        assert [p.profile_id for p in active.profiles] == ["alpha"]

    def test_env_file_fallback_activates_slot(self, monkeypatch) -> None:
        monkeypatch.delenv("BETA_KEY", raising=False)
        set_ = _profile_set(
            _profile(profile_id="alpha", api_key_env="ALPHA_KEY"),
            _profile(profile_id="beta", api_key_env="BETA_KEY"),
            default="alpha",
        )
        monkeypatch.setenv("ALPHA_KEY", "sk-a")
        active = set_.active(env_file={"BETA_KEY": "sk-b"})
        assert [p.profile_id for p in active.profiles] == ["alpha", "beta"]

    def test_mock_profiles_always_active(self) -> None:
        set_ = ProviderProfileSet(
            profiles=[
                _profile(
                    profile_id="m1", provider="mock", api_key_env="MOCK_PLACEHOLDER"
                ),
                _profile(profile_id="real", api_key_env="REAL_KEY"),
            ],
            default_profile="m1",
        )
        active = set_.active()
        assert [p.profile_id for p in active.profiles] == ["m1"]

    def test_default_profile_missing_credential_fails_closed(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("ALPHA_KEY", raising=False)
        monkeypatch.setenv("BETA_KEY", "sk-b")
        set_ = _profile_set(
            _profile(profile_id="alpha", api_key_env="ALPHA_KEY"),
            _profile(profile_id="beta", api_key_env="BETA_KEY"),
            default="alpha",
        )
        with pytest.raises(ValueError, match="default_profile"):
            set_.active()

    def test_active_set_revalidates(self, monkeypatch) -> None:
        monkeypatch.setenv("ALPHA_KEY", "sk-a")
        set_ = _profile_set(
            _profile(profile_id="alpha", api_key_env="ALPHA_KEY"),
            _profile(profile_id="beta", api_key_env="BETA_KEY"),
            default="alpha",
        )
        active = set_.active()
        assert active.default_profile == "alpha"
        assert active.profile(None).profile_id == "alpha"


class TestEnvBackwardCompat:
    def test_env_single_provider_synthesizes_one_profile(self) -> None:
        env_file = {
            "BOOK2SKILL_LLM": "compatible",
            "LLM_API_KEY": "sk-x",
            "LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "LLM_MODEL": "qwen-plus",
        }
        # shell env empty so resolution comes solely from env_file
        profiles = profile_set_from_env(env_file, environment={})
        assert profiles is not None
        assert len(profiles.profiles) == 1  # type: ignore[union-attr]
        p = profiles.profiles[0]  # type: ignore[union-attr]
        assert p.profile_id == "default"
        assert p.api_key_env == "LLM_API_KEY"
        assert p.model == "qwen-plus"

    def test_env_mock_returns_none(self) -> None:
        assert profile_set_from_env({}, environment={"BOOK2SKILL_LLM": "mock"}) is None


class TestYamlLoading:
    def test_load_example_template_is_valid(self, tmp_path: Path) -> None:
        # The shipped example must parse and validate.
        root = Path(__file__).resolve().parents[2]
        loaded = load_profile_set(root / "llm-profiles.example.yaml")
        assert loaded.default_profile == "channel-1"
        ids = {p.profile_id for p in loaded.profiles}
        assert {"channel-1", "channel-2", "channel-3", "channel-4", "channel-5"} <= ids

    def test_load_minimal_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        path.write_text(
            "schema_version: 1\n"
            "default_profile: a\n"
            "profiles:\n"
            "  - profile_id: a\n"
            "    provider: mock\n"
            "    model: mock-rule-based-v1\n"
            "    api_key_env: MOCK_PLACEHOLDER\n"
            "    roles: [map]\n",
            encoding="utf-8",
        )
        loaded = load_profile_set(path)
        assert loaded.profile("a").provider == "mock"


class TestCacheKeyScope:
    def test_different_profile_id_does_not_share_cache(self) -> None:
        from book2skill.llm.runtime import RuntimeLLMAdapter

        cfg_a = LLMRuntimeConfig(provider="mock", profile_id="a")
        cfg_b = LLMRuntimeConfig(provider="mock", profile_id="b")
        adapter_a = RuntimeLLMAdapter(cfg_a)
        adapter_b = RuntimeLLMAdapter(cfg_b)
        loc = Locator(kind=LocatorKind.UNKNOWN)
        items = [
            ChunkItem(
                input_id="b1", source_block_id="b1", text="hello", locator=loc
            )
        ]
        assert adapter_a._cache_key("src", items, "ext") != adapter_b._cache_key(
            "src", items, "ext"
        )

    def test_routing_strategy_version_in_scope(self) -> None:
        from book2skill.llm.runtime import RuntimeLLMAdapter

        cfg = LLMRuntimeConfig(provider="mock", profile_id="a")
        adapter = RuntimeLLMAdapter(cfg)
        loc = Locator(kind=LocatorKind.UNKNOWN)
        items = [ChunkItem(input_id="b1", source_block_id="b1", text="hi", locator=loc)]
        base = adapter._cache_key("src", items, "ext")
        # Mutate the strategy version: a different strategy must change the key.
        cfg2 = LLMRuntimeConfig(
            provider="mock", profile_id="a", routing_strategy_version="balanced-v1"
        )
        adapter2 = RuntimeLLMAdapter(cfg2)
        assert base != adapter2._cache_key("src", items, "ext")

    def test_locale_in_cache_scope(self) -> None:
        from book2skill.llm.runtime import RuntimeLLMAdapter

        cfg = LLMRuntimeConfig(provider="mock", profile_id="a", locale="zh-CN")
        adapter = RuntimeLLMAdapter(cfg)
        loc = Locator(kind=LocatorKind.UNKNOWN)
        items = [ChunkItem(input_id="b1", source_block_id="b1", text="hi", locator=loc)]
        base = adapter._cache_key("src", items, "ext")
        # A different content locale must not reuse English/Chinese stale cache.
        cfg2 = LLMRuntimeConfig(provider="mock", profile_id="a", locale="en")
        adapter2 = RuntimeLLMAdapter(cfg2)
        assert base != adapter2._cache_key("src", items, "ext")

    def test_structure_in_synthesis_cache_scope(self) -> None:
        from book2skill.llm.runtime import RuntimeLLMAdapter

        cfg = LLMRuntimeConfig(provider="mock", profile_id="a")
        adapter = RuntimeLLMAdapter(cfg)
        candidates: list[dict[str, object]] = [
            {"unit_id": "u1", "kind": "technique", "content": "x"},
        ]
        base = adapter._synthesis_cache_key("src", "book", candidates)
        # A different detected structure must not reuse a stale reduction.
        grounded = adapter._synthesis_cache_key(
            "src", "book", candidates, [{"heading": "第一章 始计"}]
        )
        assert base != grounded
        # Same structure yields the same key (stable across calls).
        repeat = adapter._synthesis_cache_key(
            "src", "book", candidates, [{"heading": "第一章 始计"}]
        )
        assert grounded == repeat


class TestJsonSchemaConsistency:
    """Pydantic and the JSON Schema reject the same malformed payloads."""

    def _schema(self) -> dict:
        import json

        from book2skill.resources import schema_file

        return json.loads(schema_file("llm-profiles.schema.json").read_text("utf-8"))

    def _validate_json(self, payload: dict) -> None:
        import jsonschema

        jsonschema.validate(payload, self._schema())

    def test_valid_payload_passes_both(self) -> None:
        payload = {
            "schema_version": 1,
            "default_profile": "a",
            "profiles": [
                {
                    "profile_id": "a",
                    "provider": "openai",
                    "model": "m",
                    "api_key_env": "LLM_API_KEY",
                    "roles": ["map"],
                }
            ],
        }
        self._validate_json(payload)
        ProviderProfileSet.model_validate(payload)

    def test_extra_field_rejected_by_both(self) -> None:
        payload = {
            "schema_version": 1,
            "default_profile": "a",
            "profiles": [
                {
                    "profile_id": "a",
                    "provider": "openai",
                    "model": "m",
                    "api_key_env": "LLM_API_KEY",
                    "roles": ["map"],
                    "api_key": "sk-leaked",
                }
            ],
        }
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            self._validate_json(payload)
        with pytest.raises(ValidationError):
            ProviderProfileSet.model_validate(payload)


class TestCliProfileSelection:
    """--llm-profiles + --llm-profile selects a profile end-to-end (mock)."""

    def test_mock_profile_via_cli(self, tmp_path: Path, monkeypatch) -> None:
        from typer.testing import CliRunner

        from book2skill.cli import app

        path = tmp_path / "profiles.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "default_profile": "mock-offline",
                    "profiles": [
                        {
                            "profile_id": "mock-offline",
                            "provider": "mock",
                            "model": "mock-rule-based-v1",
                            "api_key_env": "MOCK_PLACEHOLDER",
                            "roles": ["map", "skill"],
                        }
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
                "--llm-profile",
                "mock-offline",
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
        manifest = envelope.get("analysis_run") or {}
        assert manifest.get("profile_id") == "mock-offline"

    def test_unknown_profile_id_rejected(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from book2skill.cli import app

        path = tmp_path / "profiles.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "default_profile": "mock-offline",
                    "profiles": [
                        {
                            "profile_id": "mock-offline",
                            "provider": "mock",
                            "model": "mock-rule-based-v1",
                            "api_key_env": "MOCK_PLACEHOLDER",
                            "roles": ["map"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "analyze",
                str(tmp_path / "x.txt"),
                "--llm-profiles",
                str(path),
                "--llm-profile",
                "does-not-exist",
            ],
        )
        assert result.exit_code != 0
