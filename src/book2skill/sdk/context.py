"""Extension runtime context.

:class:`ExtensionContext` is handed to an extension when it activates. It
carries configuration, logging, the data root, a scratch directory and the
permissions the extension is allowed to exercise. Extensions are granted only
what their manifest declares — they must not touch Core internals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from book2skill.sdk.registrar import ExtensionRegistrar

_DEFAULT_PERMISSIONS: frozenset[str] = frozenset(
    {"read_normalized_sources", "write_extension_data"}
)


@dataclass(frozen=True)
class ExtensionContext:
    """Immutable runtime context granted to one extension instance.

    Attributes:
        extension_id: The extension's stable identifier.
        version: The resolved extension version.
        data_root: Where the extension may persist its own data (versioned).
        tmp_dir: Scratch directory for the current process. Deleted on clean exit.
        config: Read-only configuration view (from ``config.example.yaml`` style keys).
        permissions: Permissions the extension is allowed to exercise.
        dependencies: IDs of other extensions this one depends on (resolved).
        registrar: Core-owned typed registrar for declared runtime contributions.
        log: Logger namespaced to the extension.
    """

    extension_id: str
    version: str
    data_root: Path
    tmp_dir: Path
    config: dict[str, object] = field(default_factory=dict)
    permissions: frozenset[str] = _DEFAULT_PERMISSIONS
    dependencies: frozenset[str] = frozenset()
    registrar: ExtensionRegistrar | None = None
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("book2skill"))

    @property
    def scoped_logger(self) -> logging.Logger:
        """Return an extension-scoped child logger for *extension_id*."""
        return logging.getLogger(f"book2skill.extensions.{self.extension_id}")

    def has_permission(self, permission: str) -> bool:
        """Return ``True`` if the extension may exercise *permission*."""
        return permission in self.permissions

    def require_permission(self, permission: str) -> None:
        """Raise :class:`PermissionError` when the permission is not granted."""
        if not self.has_permission(permission):
            raise PermissionError(
                f"extension {self.extension_id!r} lacks required permission "
                f"{permission!r}"
            )
