"""Claude Code installer (TASK-017).

Installs a Skill into the Claude Code skills directory:

- User-level: ``~/.claude/skills/<skill_name>/``
- Project-level: ``<project_root>/.claude/skills/<skill_name>/``

Both layouts follow ``SKILL_DEPLOYMENT.md`` §2. The Claude host has no
overlay requirements — the standard Skill tree is consumed as-is.
"""

from __future__ import annotations

from pathlib import Path

from book2skill.hosts.base import HostInstaller


class ClaudeInstaller(HostInstaller):
    """Installer for Claude Code (personal or project-level)."""

    host_kind = "claude"

    def _target_path(self, skill_name: str) -> Path:
        if self._project_level:
            return self._resolve_project_target(
                ".claude", "skills", skill_name
            )
        return Path.home() / ".claude" / "skills" / skill_name


__all__ = ["ClaudeInstaller"]
