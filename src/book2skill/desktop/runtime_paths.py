"""User-data paths used by the optional Windows desktop application."""

from __future__ import annotations

import os
from pathlib import Path


def application_data_root() -> Path:
    """Return the per-user Book2Skill data root without creating it.

    ``BOOK2SKILL_HOME`` is an explicit portable-mode override.  On Windows
    the normal location is ``%LOCALAPPDATA%\\Book2Skill``; the home fallback
    keeps unit tests and source checkouts deterministic on other systems while
    this release only ships a Windows desktop installer.
    """

    configured = os.environ.get("BOOK2SKILL_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (Path(local_app_data) / "Book2Skill").resolve()
    return (Path.home() / ".book2skill").resolve()


def desktop_data_home() -> Path:
    """Return the Raw/Schema/cache root used by desktop application jobs."""

    return application_data_root() / "workspace"


def desktop_output_root() -> Path:
    """Return the visible desktop output root."""

    return application_data_root() / "output"


__all__ = ["application_data_root", "desktop_data_home", "desktop_output_root"]
