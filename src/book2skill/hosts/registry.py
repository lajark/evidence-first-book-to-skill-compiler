"""Installer factory (TASK-017).

Maps a host identifier (``claude`` | ``trae`` | ``codex`` | ``project``) to
the corresponding :class:`~book2skill.hosts.base.HostInstaller` subclass.
The CLI uses this to translate the ``--host`` flag into a concrete installer
without leaking host-specific knowledge out of the :mod:`book2skill.hosts`
package.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from book2skill.hosts.base import HostInstaller
from book2skill.hosts.chatgpt import ChatGPTInstaller
from book2skill.hosts.claude import ClaudeInstaller
from book2skill.hosts.codex import CodexInstaller
from book2skill.hosts.project import ProjectInstaller
from book2skill.hosts.trae import TraeInstaller

#: Canonical host identifiers, matching ``PRD FR-04`` / ``SKILL_DEPLOYMENT.md``.
HOST_KINDS: tuple[str, ...] = ("claude", "trae", "codex", "project", "chatgpt")


def get_installer(
    host: str,
    *,
    project_level: bool = False,
    project_root: Path | None = None,
    backup_root: Path | None = None,
    now: Callable[[], datetime] | None = None,
    target_dir: str | None = None,
) -> HostInstaller:
    """Return a fresh :class:`HostInstaller` for *host*.

    Args:
        host: One of :data:`HOST_KINDS`.
        project_level: Install into the project's host directory.
        project_root: Project root for project-level installs.
        backup_root: Root for timestamped backups.
        now: Optional clock for deterministic timestamps in tests.
        target_dir: Subdirectory under project root (project host only).

    Raises:
        ValueError: When *host* is not in :data:`HOST_KINDS`, or when
            *target_dir* is rejected by the project installer.
    """
    # Common kwargs shared by all installers.
    common: dict[str, Any] = {"project_level": project_level}
    if project_root is not None:
        common["project_root"] = project_root
    if backup_root is not None:
        common["backup_root"] = backup_root
    if now is not None:
        common["now"] = now

    if host == "claude":
        return ClaudeInstaller(**common)
    if host == "trae":
        return TraeInstaller(**common)
    if host == "codex":
        return CodexInstaller(**common)
    if host == "chatgpt":
        return ChatGPTInstaller(**common)
    if host == "project":
        if target_dir is not None:
            return ProjectInstaller(target_dir=target_dir, **common)
        return ProjectInstaller(**common)
    raise ValueError(
        f"Unknown host '{host}'. Expected one of: {', '.join(HOST_KINDS)}."
    )


__all__ = ["get_installer", "HOST_KINDS"]
