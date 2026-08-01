"""Extension dependency resolution.

Installer orders installations by dependency topology (Core then extensions)
and refuses actions that would violate declared compatibility ranges or leave
a downstream extension without a dependency. This module is pure: it decides an
order and validates constraints against a registry, without touching the
filesystem.
"""

from __future__ import annotations

from collections.abc import Iterable

from book2skill.extensions.registry import ExtensionRegistry, InstallRecord
from book2skill.extensions.version import CORE_VERSION, Version, satisfies_range
from book2skill.sdk import ExtensionManifest


class ResolutionError(Exception):
    """Raised when a dependency graph cannot be satisfied."""


def check_core_compatibility(book2skill_range: str) -> None:
    """Raise :class:`ResolutionError` if the Core version is out of range."""
    if not book2skill_range.strip():
        return
    if not satisfies_range(Version.parse(CORE_VERSION), book2skill_range):
        raise ResolutionError(
            f"Core version {CORE_VERSION} does not satisfy required "
            f"range {book2skill_range!r}"
        )


def install_order(
    manifests: Iterable[ExtensionManifest], registry: ExtensionRegistry
) -> list[str]:
    """Return extension ids ordered so every dependency precedes its dependant.

    Args:
        manifests: Iterable of :class:`ExtensionManifest` (the ones being installed).
        registry: For already-installed extensions (their declared ranges + versions).

    Raises:
        ResolutionError: on unknown dependency, version incompatibility, or cycle.
    """
    by_id: dict[str, ExtensionManifest] = {}
    for m in manifests:
        by_id[m.extension_id] = m

    installed: dict[str, InstallRecord] = {
        i: rec
        for i in registry.installed_ids()
        if (rec := registry.record(i)) is not None
    }

    # Visiting states: 0=unvisited, 1=in-progress, 2=done.
    state: dict[str, int] = {}
    order: list[str] = []

    def visit(node: str) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            raise ResolutionError(f"circular extension dependency involving {node}")
        state[node] = 1

        manifest = by_id.get(node)
        if manifest is not None:
            for dep in manifest.extension_dependencies():
                dep_id = dep.extension_id
                if dep_id not in by_id and dep_id not in installed:
                    raise ResolutionError(
                        f"{node} depends on {dep_id}, which is not installed"
                    )
                _check_dep_version(dep_id, dep.version, by_id, installed)
                visit(dep_id)

        state[node] = 2
        order.append(node)

    for node in sorted(by_id):
        visit(node)
    return order


def _check_dep_version(
    dep_id: str,
    required: str,
    by_id: dict[str, ExtensionManifest],
    installed: dict[str, InstallRecord],
) -> None:
    """Ensure the providing extension version satisfies *required* range."""
    if dep_id in by_id:
        candidate = by_id[dep_id].version
        if not satisfies_range(Version.parse(candidate), required):
            raise ResolutionError(
                f"{dep_id} {candidate} does not satisfy dependency range {required!r}"
            )
        return
    rec = installed.get(dep_id)
    if rec is not None and not satisfies_range(
        Version.parse(rec.active_version), required
    ):
        raise ResolutionError(
            f"{dep_id} {rec.active_version} does not satisfy dependency "
            f"range {required!r}"
        )
