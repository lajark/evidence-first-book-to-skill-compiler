# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the optional Windows desktop application."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]
webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")

datas = [
    (str(PROJECT_ROOT / "src" / "book2skill" / "desktop" / "static"), "book2skill/desktop/static"),
    (str(PROJECT_ROOT / "schemas"), "book2skill/_resources/schemas"),
    (str(PROJECT_ROOT / "templates" / "generated-skill"), "book2skill/_resources/templates/generated-skill"),
    (str(PROJECT_ROOT / "README.md"), "."),
    (str(PROJECT_ROOT / "LICENSE"), "."),
    (str(PROJECT_ROOT / "THIRD_PARTY_NOTICES.md"), "."),
] + webview_datas

a = Analysis(
    [str(PROJECT_ROOT / "src" / "book2skill" / "desktop" / "app.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=webview_binaries,
    datas=datas,
    hiddenimports=["book2skill.desktop.app", "book2skill.desktop.server", *webview_hiddenimports],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Optional high-risk/heavy backends remain external to the standard
        # desktop profile; the extractor's fallback chain stays available.
        "fitz",
        "pymupdf",
        "ebooklib",
        "docling",
        "IPython",
        "jupyter",
        "mypy",
        "pytest",
        "ruff",
        "torch",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Book2Skill",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Book2Skill",
)
