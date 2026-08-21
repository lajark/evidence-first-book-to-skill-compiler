from __future__ import annotations

import pytest

from book2skill.desktop.service import DesktopApplicationService, DesktopRequestError


def test_bootstrap_exposes_windows_paths_without_creating_them(tmp_path) -> None:
    service = DesktopApplicationService(data_root=tmp_path / "app")

    payload = service.bootstrap()

    assert payload["platform"] == "windows"
    assert payload["offline_default"] is True
    assert not (tmp_path / "app").exists()


def test_bootstrap_exposes_desktop_configuration_paths(tmp_path) -> None:
    service = DesktopApplicationService(data_root=tmp_path / "app")

    paths = service.bootstrap()["paths"]

    assert paths["env_file"] == str(tmp_path / "app" / ".env")
    assert paths["profiles_file"] == str(
        tmp_path / "app" / "llm-profiles.local.yaml"
    )


def test_desktop_llm_uses_data_root_configuration(
    monkeypatch, tmp_path
) -> None:
    captured = {}

    def fake_adapter(kind, **kwargs):
        captured.update(kind=kind, **kwargs)
        return object()

    monkeypatch.setattr("book2skill.desktop.service.build_llm_adapter", fake_adapter)
    root = tmp_path / "app"
    root.mkdir()
    (root / ".env").write_text("LLM_API_KEY=not-read-by-test\n", encoding="utf-8")
    (root / "llm-profiles.local.yaml").write_text("profiles: []\n", encoding="utf-8")

    service = DesktopApplicationService(data_root=root)
    service._llm({"llm": "mock"}, root / "workspace")

    assert captured["env_file_path"] == root / ".env"
    assert captured["llm_profiles"] == str(root / "llm-profiles.local.yaml")


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
