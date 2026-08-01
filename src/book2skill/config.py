"""Lightweight ``.env`` loader — no third-party dependency.

Loads ``KEY=VALUE`` pairs from a ``.env`` file into a plain dict (it does
**not** mutate ``os.environ``). Real environment variables and shell exports
are consulted separately by the caller, so the lookup priority becomes:
CLI flag > system environment variable > ``.env`` file > built-in default.

The loader searches for ``.env`` starting from the current working directory
and walking up to the filesystem root (so commands run from a project
subdirectory still find the project-root ``.env``). A missing file is a
silent no-op — the pipeline stays fully offline-capable.

Format rules
------------
- One ``KEY=VALUE`` per line.
- Leading/trailing whitespace around key and value is stripped.
- Lines starting with ``#`` and blank lines are ignored.
- A value wrapped in matching single or double quotes keeps inner content
  verbatim (quotes are stripped). Inline ``#`` is *not* treated as a comment
  inside a value, to avoid surprising users whose secrets contain ``#``.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_env_file"]


def _parse_line(line: str) -> tuple[str, str] | None:
    """Parse a single ``.env`` line into a ``(key, value)`` pair.

    Returns ``None`` for blank lines, comments, and lines without ``=``.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    # Strip one matching pair of surrounding quotes (single or double).
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def _find_env_file(start: Path) -> Path | None:
    """Walk up from *start* to the root, returning the first ``.env`` found."""
    start = start.resolve()
    for candidate in [start, *start.parents]:
        env_path = candidate / ".env"
        if env_path.is_file():
            return env_path
    return None


def load_env_file(
    path: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Load ``.env`` into a dict without touching ``os.environ``.

    Args:
        path: Explicit ``.env`` path. When omitted, searches from the
            current working directory upward.

    Returns:
        A mapping of the parsed keys to their values. Empty when no file is
        found. Later duplicate keys in the file overwrite earlier ones.
    """
    if path is not None:
        env_path = Path(path)
        if not env_path.is_file():
            return {}
    else:
        found = _find_env_file(Path.cwd())
        if found is None:
            return {}
        env_path = found

    loaded: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_line(line)
        if parsed is not None:
            loaded[parsed[0]] = parsed[1]
    return loaded
