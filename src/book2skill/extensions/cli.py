"""``book2skill extensions`` CLI command group (FR-11 / B2S-M5-03).

Manages extension lifecycle: inspect, install, list, doctor, upgrade,
rollback and uninstall. The registry lives under ``BOOK2SKILL_HOME/extensions``
(or ``~/.book2skill/extensions`` by default).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from book2skill.extensions import (
    ExtensionError,
    ExtensionManager,
    inspect_package,
)
from book2skill.extensions.installer import LifecycleResult

extensions_app = typer.Typer(
    name="extensions", help="Manage Book2Skill extensions (FR-11)."
)
_console = Console()


def _registry_root() -> Path:
    home = os.environ.get("BOOK2SKILL_HOME")
    base = Path(home) if home else Path.home() / ".book2skill"
    return base / "extensions"


def _manager(registry_root: Path) -> ExtensionManager:
    return ExtensionManager(registry_root, data_home=registry_root.parent / "data")


def _fmt_result(res: LifecycleResult) -> dict[str, Any]:
    return {
        "operation": res.operation,
        "extension_id": res.extension_id,
        "version": res.version,
        "ok": res.ok,
        "message": res.message,
    }


def _print_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


@extensions_app.command(name="inspect")
def extensions_inspect(
    package: Path = typer.Argument(..., help="Extension package (.zip) or directory."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """Show an extension package's manifest and integrity summary (no install)."""
    try:
        inspected = inspect_package(package)
    except (FileNotFoundError, ValueError) as exc:
        _console.print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=1) from exc

    m = inspected.manifest
    payload = {
        "extension_id": m.extension_id,
        "version": m.version,
        "file_count": inspected.file_count,
        "payload_bytes": inspected.payload_bytes,
        "requires_book2skill": m.book2skill_range(),
        "entry_points": m.entry_points,
        "permissions": m.permissions,
    }
    if json_output:
        _print_json(payload)
        return
    _console.print(f"extension: {m.extension_id} v{m.version}")
    _console.print(f"  files: {inspected.file_count} ({inspected.payload_bytes} bytes)")
    _console.print(f"  requires book2skill: {m.book2skill_range()}")
    _console.print(f"  entry points: {', '.join(m.entry_points)}")
    if m.permissions:
        _console.print(f"  permissions: {', '.join(m.permissions)}")


@extensions_app.command(name="install")
def extensions_install(
    package: Path = typer.Argument(..., help="Extension package (.zip) or directory."),
    registry_root: Path | None = typer.Option(
        None, "--registry-root", help="Override registry location."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """Install an extension package after integrity + dependency checks."""
    manager = _manager(registry_root or _registry_root())
    try:
        result = manager.install(package)
    except (ExtensionError, ValueError) as exc:
        _console.print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if json_output:
        _print_json(_fmt_result(result))
    else:
        _console.print(
            f"[green]Installed[/green] {result.extension_id} v{result.version}"
        )


@extensions_app.command(name="upgrade")
def extensions_upgrade(
    package: Path = typer.Argument(..., help="Extension package (.zip) or directory."),
    registry_root: Path | None = typer.Option(
        None, "--registry-root", help="Override registry location."
    ),
    force: bool = typer.Option(
        False, "--force", help="Reinstall the same version (re-activate)."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """Upgrade an extension to the packaged version (side-by-side, atomic)."""
    manager = _manager(registry_root or _registry_root())
    try:
        result = manager.upgrade(package, force=force)
    except (ExtensionError, ValueError) as exc:
        _console.print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if json_output:
        _print_json(_fmt_result(result))
    else:
        _console.print(
            f"[green]Upgraded[/green] {result.extension_id} → v{result.version}"
        )


@extensions_app.command(name="list")
def extensions_list(
    registry_root: Path | None = typer.Option(
        None, "--registry-root", help="Override registry location."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """List installed extensions and their active versions."""
    manager = _manager(registry_root or _registry_root())
    rows = manager.list()
    if json_output:
        _print_json({"extensions": rows})
        return
    if not rows:
        _console.print("(no extensions installed)")
        return
    for row in rows:
        versions = ", ".join(row["versions"])  # type: ignore[arg-type]
        _console.print(
            f"[bold]{row['extension_id']}[/bold] active={row['active_version']} "
            f"versions=[{versions}]"
        )


@extensions_app.command(name="doctor")
def extensions_doctor(
    extension_id: str = typer.Argument(..., help="Installed extension id."),
    registry_root: Path | None = typer.Option(
        None, "--registry-root", help="Override registry location."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """Verify an installed extension's integrity and manifest."""
    manager = _manager(registry_root or _registry_root())
    try:
        result = manager.doctor(extension_id)
    except ExtensionError as exc:
        _console.print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if json_output:
        _print_json(_fmt_result(result))
    else:
        _console.print(
            f"[green]{result.extension_id}[/green] {result.message} "
            f"(in {result.version})"
        )


@extensions_app.command(name="rollback")
def extensions_rollback(
    extension_id: str = typer.Argument(..., help="Installed extension id."),
    registry_root: Path | None = typer.Option(
        None, "--registry-root", help="Override registry location."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """Revert an extension to its previous installed version."""
    manager = _manager(registry_root or _registry_root())
    try:
        result = manager.rollback(extension_id)
    except ExtensionError as exc:
        _console.print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if json_output:
        _print_json(_fmt_result(result))
    else:
        _console.print(
            f"[green]Rolled back[/green] {extension_id} → v{result.version}"
        )


@extensions_app.command(name="uninstall")
def extensions_uninstall(
    extension_id: str = typer.Argument(..., help="Installed extension id."),
    registry_root: Path | None = typer.Option(
        None, "--registry-root", help="Override registry location."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """Uninstall an extension (refuses when other extensions depend on it)."""
    manager = _manager(registry_root or _registry_root())
    try:
        result = manager.uninstall(extension_id)
    except ExtensionError as exc:
        _console.print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if json_output:
        _print_json(_fmt_result(result))
    else:
        _console.print(f"[green]Uninstalled[/green] {extension_id}")
