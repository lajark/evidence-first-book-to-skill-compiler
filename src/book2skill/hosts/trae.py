"""TRAE installer (TASK-017).

Installs a Skill into the TRAE project skills directory:

``<project_root>/.trae/skills/<skill_name>/``

TRAE only supports project-level installs per ``SKILL_DEPLOYMENT.md`` §3, so
the ``project_level`` flag is always treated as True.
"""

from __future__ import annotations

from pathlib import Path

from book2skill.hosts.base import HostInstaller


class TraeInstaller(HostInstaller):
    """Installer for TRAE (project-level only)."""

    host_kind = "trae"

    def _target_path(self, skill_name: str) -> Path:
        # TRAE is always project-scoped: ignore project_level flag.
        return self._resolve_project_target(".trae", "skills", skill_name)


__all__ = ["TraeInstaller"]
