"""Tests for the HostInstaller base class and parse_skill_name (TASK-017).

Covers:
- ``parse_skill_name`` happy path + every error branch.
- Install: first install, re-install with backup, dry-run, no-backup, restore
  on failure, missing SKILL.md.
- Uninstall: existing target, absent target (idempotent), dry-run, invalid
  skill name, OSError mapping.

Tests use a fake installer subclass so the install slot is under ``tmp_path``
rather than ``~/.claude`` etc., avoiding touching the real user home.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import pytest

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.hosts.base import HostInstaller, parse_skill_name

_VALID_FRONTMATTER = (
    "---\n"
    "name: my-skill\n"
    "description: A valid skill that does something useful.\n"
    "---\n"
)


class _FakeInstaller(HostInstaller):
    """Installer whose target lives under a tmp_path-derived root.

    Useful for testing the base class without touching the real home dir.
    """

    host_kind = "fake"

    def __init__(
        self,
        install_root: Path,
        *,
        fail_overlay: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._install_root = install_root.resolve()
        self._fail_overlay = fail_overlay

    def _target_path(self, skill_name: str) -> Path:
        return self._install_root / skill_name

    def _apply_overlay(self, target_dir: Path) -> None:
        if self._fail_overlay:
            raise RuntimeError("overlay failed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(skill_dir: Path, name: str = "my-skill") -> Path:
    """Create a minimal Skill directory with frontmatter + one reference file."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A valid skill that does something.\n---\n"
        "# Skill\n\nBody content here.\n",
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir(exist_ok=True)
    (skill_dir / "references" / "guide.md").write_text(
        "# Guide\n\nDetailed reference.\n", encoding="utf-8"
    )
    return skill_dir


def _fixed_clock() -> _dt.datetime:
    return _dt.datetime(2026, 7, 29, 12, 0, 0, tzinfo=_dt.UTC)


# ---------------------------------------------------------------------------
# parse_skill_name
# ---------------------------------------------------------------------------


class TestParseSkillName:
    def test_reads_name_from_frontmatter(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src", name="great-skill")
        assert parse_skill_name(skill_dir) == "great-skill"

    def test_missing_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(DomainError) as exc_info:
            parse_skill_name(tmp_path / "missing")
        assert exc_info.value.code == ErrorCode.INSTALL_SKILL_DIR_INVALID
        assert "Skill directory not found" in exc_info.value.message

    def test_missing_skill_md_raises(self, tmp_path: Path) -> None:
        (tmp_path / "skill").mkdir()
        with pytest.raises(DomainError) as exc_info:
            parse_skill_name(tmp_path / "skill")
        assert exc_info.value.code == ErrorCode.INSTALL_SKILL_DIR_INVALID
        assert "no SKILL.md" in exc_info.value.message

    def test_no_frontmatter_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# No frontmatter\n\nBody.\n")
        with pytest.raises(DomainError) as exc_info:
            parse_skill_name(skill_dir)
        assert exc_info.value.code == ErrorCode.INSTALL_SKILL_DIR_INVALID
        assert "no YAML frontmatter" in exc_info.value.message

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: [unterminated\n---\n",
            encoding="utf-8",
        )
        with pytest.raises(DomainError) as exc_info:
            parse_skill_name(skill_dir)
        assert exc_info.value.code == ErrorCode.INSTALL_SKILL_DIR_INVALID
        assert "malformed" in exc_info.value.message

    def test_missing_name_field_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: missing name field\n---\n# Body\n",
            encoding="utf-8",
        )
        with pytest.raises(DomainError) as exc_info:
            parse_skill_name(skill_dir)
        assert exc_info.value.code == ErrorCode.INSTALL_SKILL_DIR_INVALID
        assert "missing the 'name'" in exc_info.value.message

    def test_invalid_name_pattern_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: Bad_Name\ndescription: has bad chars.\n---\n# Body\n",
            encoding="utf-8",
        )
        with pytest.raises(DomainError) as exc_info:
            parse_skill_name(skill_dir)
        assert exc_info.value.code == ErrorCode.INSTALL_SKILL_DIR_INVALID
        assert "slug pattern" in exc_info.value.message

    def test_non_mapping_frontmatter_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n- just a list\n---\n# Body\n",
            encoding="utf-8",
        )
        with pytest.raises(DomainError) as exc_info:
            parse_skill_name(skill_dir)
        assert exc_info.value.code == ErrorCode.INSTALL_SKILL_DIR_INVALID
        assert "mapping" in exc_info.value.message


# ---------------------------------------------------------------------------
# HostInstaller.install
# ---------------------------------------------------------------------------


class TestInstall:
    def test_first_install_copies_files_and_no_backup(
        self, tmp_path: Path
    ) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        install_root = tmp_path / "target"
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root,
            backup_root=backup_root,
            now=_fixed_clock,
        )

        record = installer.install(skill_dir)

        assert record.action == "install"
        assert record.skill_name == "my-skill"
        assert record.target_dir == install_root / "my-skill"
        assert record.backup_path is None  # first install, no prior tree
        assert record.dry_run is False
        # Two files copied: SKILL.md + references/guide.md
        assert record.files_copied == 2
        assert (install_root / "my-skill" / "SKILL.md").exists()
        assert (install_root / "my-skill" / "references" / "guide.md").exists()

    def test_reinstall_takes_backup(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src", name="my-skill")
        # Pre-populate the install slot with an "old" version.
        install_root = tmp_path / "target"
        (install_root / "my-skill").mkdir(parents=True)
        (install_root / "my-skill" / "SKILL.md").write_text("old version\n")
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        record = installer.install(skill_dir)

        assert record.backup_path is not None
        assert record.backup_path.exists()
        assert (record.backup_path / "SKILL.md").read_text() == "old version\n"
        # New version is in place.
        body = (install_root / "my-skill" / "SKILL.md").read_text()
        assert "Body content here" in body

    def test_dry_run_does_not_touch_filesystem(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        install_root = tmp_path / "target"
        # Pretend an old version exists.
        (install_root / "my-skill").mkdir(parents=True)
        (install_root / "my-skill" / "SKILL.md").write_text("old\n")
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        record = installer.install(skill_dir, dry_run=True)

        assert record.dry_run is True
        assert record.files_copied == 0
        # Old install untouched.
        assert (install_root / "my-skill" / "SKILL.md").read_text() == "old\n"
        # No backup taken.
        assert not backup_root.exists()
        # Notes mention both the install and the backup intent.
        joined = " | ".join(record.notes)
        assert "would install" in joined
        assert "would back up" in joined

    def test_dry_run_on_empty_target_no_backup_note(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        install_root = tmp_path / "target"
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        record = installer.install(skill_dir, dry_run=True)

        joined = " | ".join(record.notes)
        assert "would install" in joined
        assert "would back up" not in joined

    def test_no_backup_deletes_old_tree(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        install_root = tmp_path / "target"
        (install_root / "my-skill").mkdir(parents=True)
        (install_root / "my-skill" / "SKILL.md").write_text("old\n")
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        record = installer.install(skill_dir, no_backup=True)

        assert record.backup_path is None
        assert not backup_root.exists()
        body = (install_root / "my-skill" / "SKILL.md").read_text()
        assert "Body content here" in body

    def test_overlay_failure_restores_previous_version(
        self, tmp_path: Path
    ) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        install_root = tmp_path / "target"
        (install_root / "my-skill").mkdir(parents=True)
        (install_root / "my-skill" / "SKILL.md").write_text("old\n")
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root,
            backup_root=backup_root,
            now=_fixed_clock,
            fail_overlay=True,
        )

        with pytest.raises(DomainError) as exc_info:
            installer.install(skill_dir)
        assert exc_info.value.code == ErrorCode.INSTALL_FAILED
        assert "Install to" in exc_info.value.message
        # Previous version restored.
        assert (install_root / "my-skill" / "SKILL.md").read_text() == "old\n"
        # Backup consumed by restore.
        assert exc_info.value.recovery is not None

    def test_install_missing_skill_dir_raises(self, tmp_path: Path) -> None:
        install_root = tmp_path / "target"
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        with pytest.raises(DomainError) as exc_info:
            installer.install(tmp_path / "missing")
        assert exc_info.value.code == ErrorCode.INSTALL_SKILL_DIR_INVALID

    def test_repeated_install_unique_backup_dirs(self, tmp_path: Path) -> None:
        """Two installs in the same clock tick get unique backup paths."""
        skill_dir = _make_skill(tmp_path / "src")
        install_root = tmp_path / "target"
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        # First install: empty slot → no backup.
        installer.install(skill_dir)
        # Second install: takes a backup at the same clock tick.
        record2 = installer.install(skill_dir)
        assert record2.backup_path is not None
        # Third install: should produce a unique suffix on the backup dir.
        record3 = installer.install(skill_dir)
        assert record3.backup_path is not None
        assert record2.backup_path != record3.backup_path
        assert record3.backup_path.exists()

    def test_installed_files_preserve_mtime(self, tmp_path: Path) -> None:
        """shutil.copy2 must preserve mtime for auditability."""
        import os

        skill_dir = _make_skill(tmp_path / "src")
        src_skill_md = skill_dir / "SKILL.md"
        # Set a recognizable mtime on the source.
        src_time = 1_700_000_000
        os.utime(src_skill_md, (src_time, src_time))

        install_root = tmp_path / "target"
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )
        installer.install(skill_dir)

        dst_skill_md = install_root / "my-skill" / "SKILL.md"
        dst_mtime = dst_skill_md.stat().st_mtime
        assert int(dst_mtime) == src_time


# ---------------------------------------------------------------------------
# HostInstaller.uninstall
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_uninstall_removes_target(self, tmp_path: Path) -> None:
        install_root = tmp_path / "target"
        target = install_root / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body\n")
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        record = installer.uninstall("my-skill")

        assert record.action == "uninstall"
        assert record.skill_name == "my-skill"
        assert record.target_dir == target
        assert not target.exists()

    def test_uninstall_absent_target_is_idempotent(self, tmp_path: Path) -> None:
        install_root = tmp_path / "target"
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        record = installer.uninstall("never-installed")

        assert record.action == "uninstall"
        assert not record.target_dir.exists()

    def test_uninstall_dry_run_does_not_remove(self, tmp_path: Path) -> None:
        install_root = tmp_path / "target"
        target = install_root / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body\n")
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        record = installer.uninstall("my-skill", dry_run=True)

        assert record.dry_run is True
        assert target.exists()
        joined = " | ".join(record.notes)
        assert "would uninstall" in joined

    def test_uninstall_dry_run_absent_target_notes(self, tmp_path: Path) -> None:
        install_root = tmp_path / "target"
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        record = installer.uninstall("absent", dry_run=True)

        joined = " | ".join(record.notes)
        assert "nothing to remove" in joined

    def test_uninstall_invalid_slug_raises(self, tmp_path: Path) -> None:
        install_root = tmp_path / "target"
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )

        with pytest.raises(DomainError) as exc_info:
            installer.uninstall("Bad_Slug")
        assert exc_info.value.code == ErrorCode.UNINSTALL_FAILED
        assert "slug pattern" in exc_info.value.message

    def test_uninstall_preserves_backups(self, tmp_path: Path) -> None:
        """Uninstall only removes the install slot, not backups."""
        skill_dir = _make_skill(tmp_path / "src")
        install_root = tmp_path / "target"
        backup_root = tmp_path / "backups"
        installer = _FakeInstaller(
            install_root, backup_root=backup_root, now=_fixed_clock
        )
        # Install twice so we have a backup.
        installer.install(skill_dir)
        backup_record = installer.install(skill_dir)
        assert backup_record.backup_path is not None
        backup_path = backup_record.backup_path

        # Now uninstall — backup must survive.
        installer.uninstall("my-skill")

        assert backup_path.exists()
        assert (backup_path / "SKILL.md").exists()
