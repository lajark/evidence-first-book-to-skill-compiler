"""Smoke tests for the CLI."""

from __future__ import annotations

from typer.testing import CliRunner

from book2skill.cli import app

runner = CliRunner()


def test_hello() -> None:
    result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert "book2skill" in result.output
