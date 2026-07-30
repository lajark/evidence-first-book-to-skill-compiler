"""Codex / OpenAI Skills installer (TASK-017).

Installs a Skill into the Codex / OpenAI Skills directory:

- User-level: ``~/.agents/skills/<skill_name>/``
- Project-level: ``<project_root>/.agents/skills/<skill_name>/``

Both layouts follow ``SKILL_DEPLOYMENT.md`` §4. The Codex host applies an
overlay that emits a minimal ``agents.md`` placeholder so the Codex Skills
discovery surface can index the Skill even before the host's own discovery
runs.
"""

from __future__ import annotations

from pathlib import Path

from book2skill.hosts.base import HostInstaller


class CodexInstaller(HostInstaller):
    """Installer for Codex / OpenAI Skills (personal or project-level)."""

    host_kind = "codex"

    def _target_path(self, skill_name: str) -> Path:
        if self._project_level:
            return self._resolve_project_target(
                ".agents", "skills", skill_name
            )
        return Path.home() / ".agents" / "skills" / skill_name

    def _apply_overlay(self, target_dir: Path) -> None:
        """Emit a minimal ``agents.md`` index file for Codex discovery.

        Idempotent: always overwrites with the canonical content so a stale
        ``agents.md`` from a previous version never lingers.
        """
        skill_md = target_dir / "SKILL.md"
        if not skill_md.exists():
            return  # Defensive: install() will roll back if SKILL.md is absent.
        agents_md = target_dir / "agents.md"
        agents_md.write_text(
            "# Codex Skills Index\n\n"
            "- See `SKILL.md` in this directory for the canonical Skill "
            "definition.\n",
            encoding="utf-8",
        )


__all__ = ["CodexInstaller"]
