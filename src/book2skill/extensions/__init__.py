"""Extension lifecycle: manifest, package, registry, resolver and installer.

Downstream extensions are the only consumers of :mod:`book2skill.sdk`; this
module is Core-internal orchestration that drives install/upgrade/rollback,
dependency resolution and integrity verification. Extensions themselves never
import this package.
"""

from __future__ import annotations

from book2skill.extensions.installer import (
    ExtensionError,
    ExtensionManager,
    LifecycleResult,
)
from book2skill.extensions.package import InspectedExtension, inspect_package
from book2skill.extensions.registry import ExtensionRegistry, InstallRecord
from book2skill.extensions.resolver import ResolutionError

__all__ = [
    "ExtensionManager",
    "ExtensionRegistry",
    "InstallRecord",
    "LifecycleResult",
    "InspectedExtension",
    "inspect_package",
    "ExtensionError",
    "ResolutionError",
]
