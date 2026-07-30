"""Tests for the installer factory (TASK-017)."""

from __future__ import annotations

from pathlib import Path

import pytest

from book2skill.hosts import (
    ClaudeInstaller,
    CodexInstaller,
    ProjectInstaller,
    TraeInstaller,
    get_installer,
)
from book2skill.hosts.base import HostInstaller
from book2skill.hosts.registry import HOST_KINDS


class TestGetInstaller:
    @pytest.mark.parametrize(
        "host,expected",
        [
            ("claude", ClaudeInstaller),
            ("trae", TraeInstaller),
            ("codex", CodexInstaller),
            ("project", ProjectInstaller),
        ],
    )
    def test_returns_correct_subclass(self, host: str, expected: type) -> None:
        installer = get_installer(host, backup_root=Path("/tmp/bk"))
        assert isinstance(installer, expected)
        assert isinstance(installer, HostInstaller)
        assert installer.host_kind == host

    def test_unknown_host_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown host"):
            get_installer("unknown")

    @pytest.mark.parametrize("host", list(HOST_KINDS))
    def test_each_host_in_host_kinds(self, host: str) -> None:
        # Sanity: HOST_KINDS exposes exactly the supported identifiers.
        assert host in HOST_KINDS

    def test_kwargs_forwarded_to_constructor(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = get_installer(
            "claude",
            project_level=True,
            project_root=project_root,
            backup_root=tmp_path / "bk",
        )
        target = installer._target_path("demo")  # noqa: SLF001
        assert target == project_root / ".claude" / "skills" / "demo"

    def test_target_dir_only_affects_project_host(self, tmp_path: Path) -> None:
        """``target_dir`` kwarg only makes sense for the project host.

        Other hosts ignore it; passing it should not crash. This documents
        the contract that the CLI's --target-dir flag is project-only.
        """
        installer = get_installer("project", target_dir="vendor/skills")
        assert installer._target_dir == "vendor/skills"  # noqa: SLF001
