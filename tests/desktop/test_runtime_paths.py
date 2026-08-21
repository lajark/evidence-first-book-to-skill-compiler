from __future__ import annotations

from pathlib import Path

from book2skill.desktop.runtime_paths import application_data_root


def test_application_data_root_honours_book2skill_home(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "portable"
    monkeypatch.setenv("BOOK2SKILL_HOME", str(home))

    assert application_data_root() == home.resolve()


def test_application_data_root_uses_local_app_data_on_windows(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BOOK2SKILL_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    assert application_data_root() == (
        tmp_path / "LocalAppData" / "Book2Skill"
    ).resolve()
