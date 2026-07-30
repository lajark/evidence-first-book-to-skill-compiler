"""CLI integration tests for the ``install`` and ``uninstall`` commands (TASK-017)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from book2skill.cli import app

_VALID_FRONTMATTER = (
    "---\n"
    "name: my-skill\n"
    "description: A valid skill that does something useful.\n"
    "---\n"
)

runner = CliRunner()


def _make_skill(skill_dir: Path, name: str = "my-skill") -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A valid skill.\n---\n# Body\n",
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("guide\n")
    return skill_dir


class TestInstallCommand:
    def test_install_to_project_host(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert "Installed" in result.stdout
        target = project_root / "skills" / "my-skill"
        assert target.exists()
        assert (target / "SKILL.md").exists()

    def test_install_to_codex_with_overlay(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "codex",
                "--project-level",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        target = project_root / ".agents" / "skills" / "my-skill"
        assert (target / "SKILL.md").exists()
        assert (target / "agents.md").exists()

    def test_install_dry_run_no_changes(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.stdout
        assert "would install" in result.stdout
        # Nothing on disk.
        assert not (project_root / "skills" / "my-skill").exists()

    def test_install_json_output(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["action"] == "install"
        assert data["skill_name"] == "my-skill"
        assert data["dry_run"] is False
        assert data["files_copied"] == 2  # SKILL.md + references/guide.md

    def test_install_invalid_skill_dir_exits_one(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(tmp_path / "missing"),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 1
        assert "INSTALL_SKILL_DIR_INVALID" in result.stdout

    def test_install_unknown_host_rejected(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "unknown",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        # Typer surfaces ValueError as a BadParameter → exit code 2.
        assert result.exit_code != 0
        # The error message may be on stdout or stderr depending on runner
        # configuration; check the combined output.
        assert "Unknown host" in result.output

    def test_install_with_existing_target_takes_backup(
        self, tmp_path: Path
    ) -> None:
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"
        # Pre-populate target with an "old" version.
        target = project_root / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("old\n")
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
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
        assert (backup_path / "SKILL.md").read_text() == "old\n"


class TestUninstallCommand:
    def test_uninstall_removes_target(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        target = project_root / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body\n")
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "my-skill",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert "Uninstalled" in result.stdout
        assert not target.exists()

    def test_uninstall_dry_run_keeps_target(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        target = project_root / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body\n")
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "my-skill",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.stdout
        assert target.exists()  # not removed

    def test_uninstall_json_output(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        target = project_root / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body\n")
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "my-skill",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["action"] == "uninstall"
        assert data["skill_name"] == "my-skill"
        assert data["dry_run"] is False

    def test_uninstall_invalid_slug_exits_one(self, tmp_path: Path) -> None:
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "Bad_Slug",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 1
        assert "UNINSTALL_FAILED" in result.stdout

    def test_uninstall_absent_target_succeeds(self, tmp_path: Path) -> None:
        """Idempotent: uninstalling an absent skill is a no-op success."""
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"
        result = runner.invoke(
            app,
            [
                "uninstall",
                "never-installed",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0


class TestInstallUninstallRoundTrip:
    def test_install_then_uninstall(self, tmp_path: Path) -> None:
        """End-to-end: install then uninstall leaves no residue."""
        skill_dir = _make_skill(tmp_path / "src")
        project_root = tmp_path / "proj"
        backup_root = tmp_path / "bk"

        # Install
        result = runner.invoke(
            app,
            [
                "install",
                str(skill_dir),
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        target = project_root / "skills" / "my-skill"
        assert target.exists()

        # Uninstall
        result = runner.invoke(
            app,
            [
                "uninstall",
                "my-skill",
                "--host",
                "project",
                "--project-root",
                str(project_root),
                "--backup-root",
                str(backup_root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert not target.exists()
