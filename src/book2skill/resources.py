"""Access packaged templates and contracts without assuming a repository path.

Released wheels embed the repository-level ``templates/`` and ``schemas/``
directories under :mod:`book2skill._resources`.  Source checkouts retain a
small fallback so contributors can run tests before building a wheel; runtime
code always resolves the installed package resource first.
"""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

_RESOURCE_PACKAGE = "book2skill._resources"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def schema_file(name: str) -> Traversable:
    """Return a packaged JSON Schema by file *name*."""
    return _resource_file("schemas", name)


def template_file(name: str) -> Traversable:
    """Return a packaged generated-Skill template by file *name*."""
    return _resource_file("templates", "generated-skill", name)


def _resource_file(*parts: str) -> Traversable:
    packaged = resources.files(_RESOURCE_PACKAGE).joinpath(*parts)
    if packaged.is_file():
        return packaged

    # Hatch force-includes these files into wheels. This branch is only for a
    # source checkout, where retaining one authoritative copy avoids drift.
    fallback = _PROJECT_ROOT.joinpath(*parts)
    if fallback.is_file():
        return fallback
    raise FileNotFoundError("Book2Skill package resource not found: " + "/".join(parts))
