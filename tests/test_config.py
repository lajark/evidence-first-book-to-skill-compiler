"""Tests for the lightweight .env loader and CLI LLM config resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from book2skill.config import (
    ConfigLoadError,
    load_app_config,
    load_env_file,
    resolve_locale,
)
from book2skill.llm.runtime import RuntimeLLMAdapter


class TestParseEnvFile:
    """Parsing rules: quotes, comments, missing file, walk-up."""

    def test_missing_file_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        # The project root may host a real .env; force the walk-up to find
        # nothing so the "no env file" branch is exercised deterministically.
        monkeypatch.setattr(
            "book2skill.config._find_env_file", lambda _start: None
        )
        assert load_env_file() == {}

    def test_explicit_missing_path_returns_empty(self, tmp_path: Path) -> None:
        assert load_env_file(tmp_path / "nope.env") == {}

    def test_basic_key_value(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(
            "BOOK2SKILL_LLM=openai\nOPENAI_MODEL=gpt-4o-mini\n", encoding="utf-8"
        )
        assert load_env_file(env) == {
            "BOOK2SKILL_LLM": "openai",
            "OPENAI_MODEL": "gpt-4o-mini",
        }

    def test_comments_and_blank_lines_ignored(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(
            "# this is a comment\n\n   \nKEY=value\n# another\n",
            encoding="utf-8",
        )
        assert load_env_file(env) == {"KEY": "value"}

    def test_strips_surrounding_quotes(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(
            'A="double quoted"\nB=\'single quoted\'\n',
            encoding="utf-8",
        )
        assert load_env_file(env) == {
            "A": "double quoted",
            "B": "single quoted",
        }

    def test_inline_hash_is_part_of_value(self, tmp_path: Path) -> None:
        # Inline # is NOT a comment, so secrets containing # survive.
        env = tmp_path / ".env"
        env.write_text("KEY=sk-abc#def\n", encoding="utf-8")
        assert load_env_file(env) == {"KEY": "sk-abc#def"}

    def test_duplicate_keys_last_wins(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("K=1\nK=2\n", encoding="utf-8")
        assert load_env_file(env) == {"K": "2"}

    def test_walks_up_to_find_env(self, tmp_path: Path, monkeypatch) -> None:
        # .env lives at project root; command runs from a subdir.
        (tmp_path / ".env").write_text("BOOK2SKILL_LLM=openai\n", encoding="utf-8")
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        assert load_env_file() == {"BOOK2SKILL_LLM": "openai"}

    def test_does_not_mutate_os_environ(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("BOOK2SKILL_TEST_KEY", raising=False)
        env = tmp_path / ".env"
        env.write_text("BOOK2SKILL_TEST_KEY=secret\n", encoding="utf-8")
        loaded = load_env_file(env)
        assert loaded == {"BOOK2SKILL_TEST_KEY": "secret"}
        # The loader must not pollute os.environ.
        import os

        assert os.environ.get("BOOK2SKILL_TEST_KEY") is None


class TestLocaleResolution:
    """Locale accepts stable tags and keeps the documented priority order."""

    def test_defaults_to_simplified_chinese(self) -> None:
        assert resolve_locale(environment={}, env_file={}) == "zh-CN"

    def test_cli_value_beats_environment_and_env_file(self) -> None:
        assert (
            resolve_locale(
                "en",
                environment={"BOOK2SKILL_LOCALE": "zh-CN"},
                env_file={"BOOK2SKILL_LOCALE": "zh-CN"},
            )
            == "en"
        )

    def test_alias_and_invalid_value_handling(self) -> None:
        assert resolve_locale(environment={"BOOK2SKILL_LOCALE": "en_US"}) == "en"
        try:
            resolve_locale(environment={"BOOK2SKILL_LOCALE": "fr"})
        except ValueError as exc:
            assert "zh-CN, en" in str(exc)
        else:  # pragma: no cover - assertion guard
            raise AssertionError("unsupported locale must fail")


class TestAppConfigContract:
    """The explicit YAML contract rejects drift before runtime use."""

    def test_loads_example_shape_and_resolves_relative_paths(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "schema_version: 1\n"
            "language: zh-CN\n"
            "data_home: ./workspace\n"
            "default_mode: analyze\n"
            "network: disabled\n"
            "cloud_llm:\n  enabled: false\n"
            "limits:\n  max_input_mb: 200\n"
            "extensions:\n  supported_manifest_versions: [1]\n"
            "hosts: [claude, trae, codex]\n",
            encoding="utf-8",
        )

        config = load_app_config(config_path)

        assert config.data_home == (tmp_path / "workspace").resolve()
        assert config.network == "disabled"
        assert config.hosts == ["claude", "trae", "codex"]

    def test_rejects_unknown_keys_without_echoing_values(self, tmp_path: Path) -> None:
        config_path = tmp_path / "bad.yaml"
        config_path.write_text(
            "schema_version: 1\nlanguage: zh-CN\nunknown: secret-value\n",
            encoding="utf-8",
        )

        with pytest.raises(ConfigLoadError) as exc_info:
            load_app_config(config_path)

        assert "unknown" in str(exc_info.value)
        assert "secret-value" not in str(exc_info.value)

    def test_cloud_llm_enabled_requires_endpoint_and_model(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "bad-cloud.yaml"
        config_path.write_text(
            "cloud_llm:\n  enabled: true\n", encoding="utf-8"
        )

        with pytest.raises(ConfigLoadError, match="cloud_llm"):
            load_app_config(config_path)

    def test_cloud_llm_requires_explicit_network_opt_in(self, tmp_path: Path) -> None:
        config_path = tmp_path / "cloud-offline.yaml"
        config_path.write_text(
            "network: disabled\n"
            "cloud_llm:\n"
            "  enabled: true\n"
            "  base_url: http://localhost/v1\n"
            "  model: local\n",
            encoding="utf-8",
        )

        with pytest.raises(ConfigLoadError, match="network"):
            load_app_config(config_path)

    def test_cli_config_supplies_locale_and_default_data_home(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("book2skill.config._find_env_file", lambda _start: None)
        monkeypatch.delenv("BOOK2SKILL_LOCALE", raising=False)
        source = tmp_path / "notes.txt"
        source.write_text("A principle with a source.", encoding="utf-8")
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "language: en\ndata_home: ./runtime\n", encoding="utf-8"
        )

        from book2skill.cli import app

        result = CliRunner().invoke(
            app,
            ["--config", str(config_path), "analyze", str(source), "--json"],
        )

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["analysis_run"]["locale"] == "en"
        source_id = payload["source_ids"][0]
        assert (
            tmp_path / "runtime" / "raw" / source_id / "1" / "manifest.json"
        ).is_file()

    def test_cli_validate_json_is_machine_readable(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("schema_version: 1\n", encoding="utf-8")

        from book2skill.cli import app

        result = CliRunner().invoke(
            app, ["config", "validate", str(config_path), "--json"]
        )

        assert result.exit_code == 0
        assert '"valid": true' in result.stdout


class TestCliLlmResolution:
    """_build_llm_adapter resolves config from .env with correct priority."""

    def test_env_file_provides_openai_config(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Ensure clean shell env so resolution comes solely from .env.
        for k in (
            "BOOK2SKILL_LLM",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "BOOK2SKILL_LLM=openai\n"
            "OPENAI_API_KEY=sk-from-file\n"
            "OPENAI_BASE_URL=http://localhost:11434/v1\n"
            "OPENAI_MODEL=qwen2.5:7b\n",
            encoding="utf-8",
        )

        from book2skill.cli import _build_llm_adapter
        from book2skill.llm.runtime import RuntimeLLMAdapter

        adapter = _build_llm_adapter(None)
        assert isinstance(adapter, RuntimeLLMAdapter)
        assert adapter.config.model == "qwen2.5:7b"
        assert adapter.config.api_key == "sk-from-file"
        assert adapter.config.base_url == "http://localhost:11434/v1"

    def test_cli_flag_overrides_env_file(self, tmp_path: Path, monkeypatch) -> None:
        for k in ("BOOK2SKILL_LLM", "OPENAI_MODEL"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "BOOK2SKILL_LLM=openai\nOPENAI_MODEL=from-file\n",
            encoding="utf-8",
        )

        from book2skill.cli import _build_llm_adapter

        # Explicit --llm mock beats .env's openai.
        adapter = _build_llm_adapter("mock")
        assert isinstance(adapter, RuntimeLLMAdapter)
        assert adapter.config.provider == "mock"

    def test_shell_env_overrides_env_file(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # The autouse fixture pins BOOK2SKILL_LLM=mock at suite scope; drop it
        # so the .env's openai setting is the active provider, then verify
        # shell OPENAI_* values override the file's values.
        monkeypatch.delenv("BOOK2SKILL_LLM", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "BOOK2SKILL_LLM=openai\nOPENAI_MODEL=from-file\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENAI_MODEL", "from-shell")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-shell")

        from book2skill.cli import _build_llm_adapter
        from book2skill.llm.runtime import RuntimeLLMAdapter

        adapter = _build_llm_adapter(None)
        assert isinstance(adapter, RuntimeLLMAdapter)
        assert adapter.config.model == "from-shell"

    def test_defaults_to_mock_without_config(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        for k in (
            "BOOK2SKILL_LLM",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.chdir(tmp_path)
        # Guard against a real project-root .env being walked into: with the
        # shell var removed, discovery of any .env would otherwise resolve a
        # real provider instead of the default Mock.
        monkeypatch.setattr(
            "book2skill.config._find_env_file", lambda _start: None
        )

        from book2skill.cli import _build_llm_adapter

        adapter = _build_llm_adapter(None)
        assert isinstance(adapter, RuntimeLLMAdapter)
        assert adapter.config.provider == "mock"

    def test_compatible_alias_routes_to_openai_adapter(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """`--llm compatible` is the generic alias for `--llm openai`."""
        for k in (
            "BOOK2SKILL_LLM",
            "LLM_API_KEY",
            "OPENAI_API_KEY",
            "LLM_BASE_URL",
            "OPENAI_BASE_URL",
            "LLM_MODEL",
            "OPENAI_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.chdir(tmp_path)

        from book2skill.cli import _build_llm_adapter
        from book2skill.llm.runtime import RuntimeLLMAdapter

        adapter = _build_llm_adapter("compatible", model="qwen-plus")
        assert isinstance(adapter, RuntimeLLMAdapter)
        assert adapter.config.model == "qwen-plus"
        assert adapter.config.api_key == "sk-test"

    def test_llm_env_vars_preferred_over_openai(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Generic LLM_* keys take precedence over legacy OPENAI_* keys."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-legacy")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://legacy/v1")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
        monkeypatch.setenv("LLM_API_KEY", "sk-generic")
        monkeypatch.setenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen-plus")
        monkeypatch.chdir(tmp_path)  # no .env file

        from book2skill.cli import _build_llm_adapter
        from book2skill.llm.runtime import RuntimeLLMAdapter

        adapter = _build_llm_adapter("compatible")
        assert isinstance(adapter, RuntimeLLMAdapter)
        assert adapter.config.api_key == "sk-generic"
        assert adapter.config.base_url == (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        assert adapter.config.model == "qwen-plus"

    def test_llm_env_vars_from_env_file(self, tmp_path: Path, monkeypatch) -> None:
        """LLM_* keys in .env are picked up (Bailian example)."""
        for k in (
            "BOOK2SKILL_LLM",
            "LLM_API_KEY",
            "LLM_BASE_URL",
            "LLM_MODEL",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "BOOK2SKILL_LLM=compatible\n"
            "LLM_API_KEY=sk-bailian\n"
            "LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
            "LLM_MODEL=qwen-plus\n",
            encoding="utf-8",
        )

        from book2skill.cli import _build_llm_adapter
        from book2skill.llm.runtime import RuntimeLLMAdapter

        adapter = _build_llm_adapter(None)
        assert isinstance(adapter, RuntimeLLMAdapter)
        assert adapter.config.api_key == "sk-bailian"
        assert adapter.config.model == "qwen-plus"
        assert adapter.config.base_url == (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
