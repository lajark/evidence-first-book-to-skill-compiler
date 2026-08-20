"""Version metadata is derived from the runtime package source."""

from __future__ import annotations

import tomllib
from pathlib import Path

from book2skill import __version__


def test_hatch_uses_runtime_version_as_its_single_source() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == (
        "src/book2skill/__init__.py"
    )
    assert __version__ == "1.0.2"
