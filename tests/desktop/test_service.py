from __future__ import annotations

import pytest

from book2skill.desktop.service import DesktopApplicationService, DesktopRequestError


def test_bootstrap_exposes_windows_paths_without_creating_them(tmp_path) -> None:
    service = DesktopApplicationService(data_root=tmp_path / "app")

    payload = service.bootstrap()

    assert payload["platform"] == "windows"
    assert payload["offline_default"] is True
    assert not (tmp_path / "app").exists()


def test_desktop_requests_require_rights_acknowledgement() -> None:
    with pytest.raises(DesktopRequestError, match="rights_note"):
        DesktopApplicationService._rights_note({})


def test_install_dry_run_delegates_to_host_installer(tmp_path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: example-skill\ndescription: Example\n---\n\n# Example\n",
        encoding="utf-8",
    )
    project_root = tmp_path / "project"
    service = DesktopApplicationService(data_root=tmp_path / "app")

    result = service.install(
        {
            "host": "project",
            "skill_dir": str(skill_dir),
            "project_level": True,
            "project_root": str(project_root),
            "dry_run": True,
        }
    )

    assert result["dry_run"] is True
    assert result["skill_name"] == "example-skill"
    assert not (project_root / "skills" / "example-skill").exists()
