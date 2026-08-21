from __future__ import annotations

from pathlib import Path

import book2skill.desktop.app as app


def test_static_dir_uses_frozen_bundle_fallback(monkeypatch, tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    static_dir = bundle_root / "book2skill" / "desktop" / "static"
    static_dir.mkdir(parents=True)

    monkeypatch.setattr(app, "__file__", str(tmp_path / "missing" / "app.py"))
    monkeypatch.setattr(app.sys, "_MEIPASS", str(bundle_root), raising=False)
    assert app._static_dir() == static_dir
