"""ChatGPT Project installer (P2 host matrix completion).

ChatGPT does not expose a standard skills directory like Claude or Codex;
deployment is manual (upload a package via the Skills / Project interface).
This installer stages the Skill tree into a predictable directory so the
user can zip and upload it:

- User-level: ``~/.chatgpt/skills/<skill_name>/``
- Project-level: ``<project_root>/.chatgpt/skills/<skill_name>/``

The staged tree is consumed as-is — no overlay is needed because ChatGPT
reads the uploaded ``SKILL.md`` directly.
"""

from __future__ import annotations

from pathlib import Path

from book2skill.hosts.base import HostInstaller


class ChatGPTInstaller(HostInstaller):
    """Installer for ChatGPT Project (personal or project-level).

    Stages the Skill tree for manual upload; ChatGPT has no programmatic
    install API, so this is a staging copy, not a live install.
    """

    host_kind = "chatgpt"

    def _target_path(self, skill_name: str) -> Path:
        if self._project_level:
            return self._resolve_project_target(
                ".chatgpt", "skills", skill_name
            )
        return Path.home() / ".chatgpt" / "skills" / skill_name


__all__ = ["ChatGPTInstaller"]
