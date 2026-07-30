"""Generic project-level installer (TASK-017).

Installs a Skill into an arbitrary project directory chosen by the caller:

``<project_root>/<target_dir>/<skill_name>/``

The caller supplies ``target_dir`` (e.g., ``skills`` or ``vendor/skills``)
via :class:`ProjectInstaller`'s ``target_dir`` constructor argument. This
host has no overlay requirements and never writes outside
``<project_root>/<target_dir>``.

Path traversal is enforced via :meth:`HostInstaller._resolve_project_target`,
so a malicious slug cannot escape the project root.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from book2skill.hosts.base import HostInstaller


class ProjectInstaller(HostInstaller):
    """Installer for a generic project-level directory.

    Parameters:
        target_dir: Subdirectory under the project root where Skills live
            (e.g., ``skills``). Defaults to ``skills``.
    """

    host_kind = "project"

    def __init__(
        self,
        *,
        target_dir: str = "skills",
        project_level: bool = False,
        project_root: Path | None = None,
        backup_root: Path | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            project_level=project_level,
            project_root=project_root,
            backup_root=backup_root,
            now=now,
        )
        # Reject absolute paths (POSIX leading '/' or Windows drive letter)
        # before stripping so a stray '/skills' cannot sneak through.
        posix = Path(target_dir).as_posix()
        if posix.startswith("/") or Path(target_dir).is_absolute():
            raise ValueError(
                f"target_dir must be a relative subdirectory name, got: {target_dir}"
            )
        # Strip leading/trailing separators and leading './' so callers can
        # pass './skills/' or 'skills' interchangeably.
        clean = posix.strip("/").lstrip("./")
        if not clean:
            raise ValueError(
                f"target_dir must be a relative subdirectory name, got: {target_dir}"
            )
        self._target_dir = clean

    def _target_path(self, skill_name: str) -> Path:
        return self._resolve_project_target(self._target_dir, skill_name)


__all__ = ["ProjectInstaller"]
