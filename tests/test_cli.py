"""Smoke tests for the CLI."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from book2skill.cli import app

runner = CliRunner()


def test_hello() -> None:
    result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert "book2skill" in result.output


def test_cli_locale_switches_human_output() -> None:
    result = runner.invoke(app, ["--locale", "en", "hello"])

    assert result.exit_code == 0
    assert "ready for M0" in result.output


def test_cli_records_effective_locale_in_analysis_manifest(tmp_path) -> None:
    source = tmp_path / "locale.txt"
    source.write_text("A principle with a source.", encoding="utf-8")

    result = runner.invoke(app, ["--locale", "en", "analyze", str(source), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["analysis_run"]["locale"] == "en"


def test_cli_does_not_accept_api_keys_in_process_arguments() -> None:
    result = runner.invoke(app, ["analyze", "sample.txt", "--llm-api-key", "secret"])

    assert result.exit_code != 0
    assert "--llm-api-key" in result.output


def test_analyze_json_failure_keeps_stdout_parseable(tmp_path) -> None:
    missing = tmp_path / "missing.txt"

    result = runner.invoke(app, ["analyze", str(missing), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "NO_VALID_SOURCES"
    assert "ERROR" not in result.stdout


def test_build_json_failure_keeps_stdout_parseable(tmp_path) -> None:
    missing = tmp_path / "missing.txt"

    result = runner.invoke(
        app,
        [
            "build",
            str(missing),
            "--name",
            "json-demo",
            "--description",
            "JSON output regression test.",
            "--use-when",
            "When testing machine output.",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "NO_VALID_SOURCES"
    assert "WARN" not in result.stdout
