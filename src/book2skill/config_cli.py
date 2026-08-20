"""Configuration contract commands for the Book2Skill CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console

from book2skill.config import ConfigLoadError, load_app_config

config_app = typer.Typer(name="config", help="Validate Book2Skill configuration.")
_console = Console()
_stderr_console = Console(stderr=True)


@config_app.command(name="validate")
def config_validate(
    path: Path = typer.Argument(  # noqa: B008
        ..., help="YAML configuration file to validate."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """Validate a top-level config file without contacting a network service."""

    try:
        config = load_app_config(path)
    except ConfigLoadError as exc:
        payload = {"valid": False, "error": str(exc)}
        if json_output:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        else:
            _stderr_console.print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=1) from exc

    payload = {
        "valid": True,
        "path": str(path),
        "schema_version": config.schema_version,
        "language": config.language,
        "data_home": str(config.data_home),
        "default_mode": config.default_mode,
        "network": config.network,
        "hosts": config.hosts,
    }
    if json_output:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return
    _console.print(f"Configuration valid: {path}")
    _console.print(f"  schema_version: {config.schema_version}")
    _console.print(f"  language: {config.language}")
    _console.print(f"  data_home: {config.data_home}")


__all__ = ["config_app", "config_validate"]
