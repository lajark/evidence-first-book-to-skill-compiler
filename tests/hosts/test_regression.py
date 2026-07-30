"""Cross-host regression tests for all four host installers.

Each host (Claude / TRAE / Codex / Project) gets the same install → verify →
upgrade → backup → uninstall lifecycle so that a change to the shared
:class:`~book2skill.hosts.base.HostInstaller` base cannot silently break one
host while the others pass.

All tests use ``project_level`` with ``project_root`` / ``backup_root``
overrides so they never touch the real user home directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from book2skill.cli import app
from book2skill.hosts.registry import HOST_KINDS

runner = CliRunner()

_SKILL_MD = (
    "---\n"
    "name: regression-skill\n"
    "description: A skill used for cross-host regression testing.\n"
    "---\n"
    "# Regression Skill\n\n"
    "## Use when\n- Testing install consistency.\n"
)

_REF_MD = "## Techniques\n- Do the thing.\n"


def _make_skill(skill_dir: Path) -> Path:
    """Build a minimal but realistic skill directory for install tests."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text(_REF_MD, encoding="utf-8")
    return skill_dir


#: Expected relative target path under project_root for each host (project-level).
_HOST_TARGET_REL: dict[str, str] = {
    "claude": ".claude/skills/regression-skill",
    "trae": ".trae/skills/regression-skill",
    "codex": ".agents/skills/regression-skill",
    "chatgpt": ".chatgpt/skills/regression-skill",
    "project": "skills/regression-skill",
}


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    return _make_skill(tmp_path / "src")


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.mark.parametrize("host", HOST_KINDS)
class TestCrossHostInstallLifecycle:
    """Full install → verify → uninstall lifecycle for every host."""

    def test_install_creates_target_with_correct_content(
        self,
        host: str,
        skill_dir: Path,
        project_root: Path,
        tmp_path: Path,
    ) -> None:
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                host,
                "--project-level",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout

        target = project_root / _HOST_TARGET_REL[host]
        assert target.exists()
        installed_md = (target / "SKILL.md").read_text(encoding="utf-8")
        assert installed_md == _SKILL_MD
        # Reference file copied too.
        assert (target / "references" / "guide.md").read_text(
            encoding="utf-8"
        ) == _REF_MD

    def test_install_json_reports_skill_name_and_file_count(
        self,
        host: str,
        skill_dir: Path,
        project_root: Path,
        tmp_path: Path,
    ) -> None:
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                host,
                "--project-level",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["action"] == "install"
        assert data["skill_name"] == "regression-skill"
        assert data["dry_run"] is False
        # Codex adds an agents.md overlay → 3 files; others copy 2.
        expected_files = 3 if host == "codex" else 2
        assert data["files_copied"] == expected_files

    def test_dry_run_leaves_nothing_on_disk(
        self,
        host: str,
        skill_dir: Path,
        project_root: Path,
        tmp_path: Path,
    ) -> None:
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                host,
                "--project-level",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        target = project_root / _HOST_TARGET_REL[host]
        assert not target.exists()

    def test_upgrade_takes_backup_of_old_version(
        self,
        host: str,
        skill_dir: Path,
        project_root: Path,
        tmp_path: Path,
    ) -> None:
        backup_root = tmp_path / "bk"
        # Pre-populate target with an "old" version.
        target = project_root / _HOST_TARGET_REL[host]
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("OLD VERSION\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                host,
                "--project-level",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["backup_path"] is not None
        backup_path = Path(data["backup_path"])
        assert backup_path.exists()
        assert (backup_path / "SKILL.md").read_text(encoding="utf-8") == "OLD VERSION\n"
        # New version is in place.
        assert (target / "SKILL.md").read_text(encoding="utf-8") == _SKILL_MD

    def test_install_then_uninstall_cleans_up(
        self,
        host: str,
        skill_dir: Path,
        project_root: Path,
        tmp_path: Path,
    ) -> None:
        backup_root = tmp_path / "bk"
        target = project_root / _HOST_TARGET_REL[host]

        # Install.
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                host,
                "--project-level",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert target.exists()

        # Uninstall.
        result = runner.invoke(
            app,
            [
                "uninstall",
                "regression-skill",
                "--host",
                host,
                "--project-level",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert not target.exists()

    def test_uninstall_absent_is_idempotent(
        self,
        host: str,
        project_root: Path,
        tmp_path: Path,
    ) -> None:
        """Uninstalling a skill that was never installed is a clean no-op."""
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "never-existed",
                "--host",
                host,
                "--project-level",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0


class TestCrossHostContentConsistency:
    """All hosts must install the identical Skill content — no host mangles
    SKILL.md or drops files."""

    def test_all_hosts_install_identical_skill_md(
        self,
        skill_dir: Path,
        project_root: Path,
        tmp_path: Path,
    ) -> None:
        installed: dict[str, str] = {}
        for host in HOST_KINDS:
            backup_root = tmp_path / f"bk-{host}"
            result = runner.invoke(
                app,
                [
                    "install",
                    str(skill_dir),
                    "--host",
                    host,
                    "--project-level",
                    "--project-root",
                    str(project_root),
                    "--backup-root",
                    str(backup_root),
                    "--json",
                ],
            )
            assert result.exit_code == 0, result.stdout
            target = project_root / _HOST_TARGET_REL[host]
            installed[host] = (target / "SKILL.md").read_text(encoding="utf-8")

        # Every host installed the same content.
        assert len(set(installed.values())) == 1
        assert list(installed.values())[0] == _SKILL_MD
