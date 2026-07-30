"""Tests for the four concrete host installers (TASK-017).

Covers:
- :class:`ClaudeInstaller` — user-level + project-level paths.
- :class:`TraeInstaller` — project-level only (flag ignored).
- :class:`CodexInstaller` — user-level + project-level + overlay (``agents.md``).
- :class:`ProjectInstaller` — relative ``target_dir`` + path traversal guard
  + invalid target_dir rejection.

All tests use ``backup_root`` and ``project_root`` overrides so they never
touch the real user home directory.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from book2skill.hosts.claude import ClaudeInstaller
from book2skill.hosts.codex import CodexInstaller
from book2skill.hosts.project import ProjectInstaller
from book2skill.hosts.trae import TraeInstaller
from book2skill.storage.errors import StoragePathError

_VALID_FRONTMATTER = (
    "---\n"
    "name: my-skill\n"
    "description: A valid skill that does something useful.\n"
    "---\n"
)


def _make_skill(skill_dir: Path, name: str = "my-skill") -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A valid skill.\n---\n# Body\n",
        encoding="utf-8",
    )
    return skill_dir


def _fixed_clock() -> _dt.datetime:
    return _dt.datetime(2026, 7, 29, 12, 0, 0, tzinfo=_dt.UTC)


# ---------------------------------------------------------------------------
# ClaudeInstaller
# ---------------------------------------------------------------------------


class TestClaudeInstaller:
    def test_user_level_target_under_home(self, tmp_path: Path) -> None:
        # We cannot easily monkeypatch Path.home; instead verify the path
        # returned by the installer matches the documented layout.
        installer = ClaudeInstaller(backup_root=tmp_path / "bk", now=_fixed_clock)
        target = installer._target_path("demo")  # noqa: SLF001 — test hook
        # ~/.claude/skills/<name>
        assert target.parts[-3:] == (".claude", "skills", "demo")

    def test_project_level_target_under_project_root(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = ClaudeInstaller(
            project_level=True,
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        target = installer._target_path("demo")  # noqa: SLF001
        assert target == project_root / ".claude" / "skills" / "demo"

    def test_install_project_level_creates_target(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = ClaudeInstaller(
            project_level=True,
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        record = installer.install(skill_dir)
        assert record.target_dir == project_root / ".claude" / "skills" / "my-skill"
        assert (record.target_dir / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# TraeInstaller
# ---------------------------------------------------------------------------


class TestTraeInstaller:
    def test_target_always_under_project_root(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = TraeInstaller(
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        target = installer._target_path("demo")  # noqa: SLF001
        assert target == project_root / ".trae" / "skills" / "demo"

    def test_project_level_flag_ignored(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = TraeInstaller(
            project_level=True,  # Should be ignored.
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        target = installer._target_path("demo")  # noqa: SLF001
        assert target == project_root / ".trae" / "skills" / "demo"

    def test_install_creates_target(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = TraeInstaller(
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        record = installer.install(skill_dir)
        assert record.target_dir == project_root / ".trae" / "skills" / "my-skill"
        assert (record.target_dir / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# CodexInstaller
# ---------------------------------------------------------------------------


class TestCodexInstaller:
    def test_user_level_target_under_agents(self, tmp_path: Path) -> None:
        installer = CodexInstaller(
            backup_root=tmp_path / "bk", now=_fixed_clock
        )
        target = installer._target_path("demo")  # noqa: SLF001
        assert target.parts[-3:] == (".agents", "skills", "demo")

    def test_project_level_target_under_project_root(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = CodexInstaller(
            project_level=True,
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        target = installer._target_path("demo")  # noqa: SLF001
        assert target == project_root / ".agents" / "skills" / "demo"

    def test_install_emits_agents_md_overlay(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = CodexInstaller(
            project_level=True,
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        record = installer.install(skill_dir)
        agents_md = record.target_dir / "agents.md"
        assert agents_md.exists()
        content = agents_md.read_text(encoding="utf-8")
        assert "Codex Skills Index" in content
        assert "SKILL.md" in content

    def test_reinstall_overwrites_stale_agents_md(self, tmp_path: Path) -> None:
        """A stale ``agents.md`` from a prior install must be replaced."""
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        # Pre-existing stale agents.md from a previous install.
        target = project_root / ".agents" / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("old\n")
        (target / "agents.md").write_text("STALE CONTENT\n")
        installer = CodexInstaller(
            project_level=True,
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        installer.install(skill_dir)
        new_agents = target / "agents.md"
        assert new_agents.read_text(encoding="utf-8") != "STALE CONTENT\n"
        assert "Codex Skills Index" in new_agents.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ProjectInstaller
# ---------------------------------------------------------------------------


class TestProjectInstaller:
    def test_default_target_dir_is_skills(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = ProjectInstaller(
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        target = installer._target_path("demo")  # noqa: SLF001
        assert target == project_root / "skills" / "demo"

    def test_custom_target_dir(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = ProjectInstaller(
            target_dir="vendor/skills",
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        target = installer._target_path("demo")  # noqa: SLF001
        assert target == project_root / "vendor" / "skills" / "demo"

    def test_install_creates_target(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = ProjectInstaller(
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        record = installer.install(skill_dir)
        assert record.target_dir == project_root / "skills" / "my-skill"
        assert (record.target_dir / "SKILL.md").exists()

    def test_path_traversal_in_skill_name_blocked(self, tmp_path: Path) -> None:
        """A malicious slug cannot escape the project root via _target_path."""
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = ProjectInstaller(
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        # The slug pattern is enforced before reaching _target_path in the
        # real install/uninstall flow, so we call _target_path directly with
        # a traversal string to assert the storage guard fires (defence in
        # depth). Need enough `..` segments to actually escape project root
        # (since `skills/../escape` collapses to inside project root).
        with pytest.raises(StoragePathError):
            installer._target_path("../../escape")  # noqa: SLF001

    def test_invalid_target_dir_absolute_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="relative"):
            ProjectInstaller(target_dir="/absolute/skills")

    def test_invalid_target_dir_empty_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="relative"):
            ProjectInstaller(target_dir="")

    def test_strip_leading_dot_slash(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        installer = ProjectInstaller(
            target_dir="./skills/",
            project_root=project_root,
            backup_root=tmp_path / "bk",
            now=_fixed_clock,
        )
        target = installer._target_path("demo")  # noqa: SLF001
        assert target == project_root / "skills" / "demo"
