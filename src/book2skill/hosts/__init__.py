"""Multi-host Skill installers (TASK-017, PRD FR-04 / FR-09).

Public entry points:

- :class:`HostInstaller` — abstract base implementing install/uninstall with
  pre-upgrade backup, atomic swap and dry-run support.
- :class:`InstallRecord` — outcome of an install/uninstall operation.
- :func:`parse_skill_name` — read the ``name`` slug from ``SKILL.md``.
- :func:`get_installer` — factory mapping a host identifier to a concrete
  installer instance.
- Concrete installers: :class:`ClaudeInstaller`, :class:`TraeInstaller`,
  :class:`CodexInstaller`, :class:`ProjectInstaller`.
- :data:`HOST_KINDS` — canonical host identifiers (``claude`` | ``trae`` |
  ``codex`` | ``project``).
"""

from __future__ import annotations

from book2skill.hosts.base import HostInstaller, InstallRecord, parse_skill_name
from book2skill.hosts.chatgpt import ChatGPTInstaller
from book2skill.hosts.claude import ClaudeInstaller
from book2skill.hosts.codex import CodexInstaller
from book2skill.hosts.project import ProjectInstaller
from book2skill.hosts.registry import HOST_KINDS, get_installer
from book2skill.hosts.trae import TraeInstaller

__all__ = [
    "HostInstaller",
    "InstallRecord",
    "parse_skill_name",
    "get_installer",
    "HOST_KINDS",
    "ClaudeInstaller",
    "ChatGPTInstaller",
    "TraeInstaller",
    "CodexInstaller",
    "ProjectInstaller",
]
