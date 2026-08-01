"""Semantic version parsing and range matching for extension dependencies.

Extensions declare a Core compatibility range (``requires.book2skill``) and
dependencies on other extensions. This module provides a small, self-contained
semver (2.0 subset) parser plus a constraint-range matcher supporting the
forms used in ``extension-manifest.json``:

- ``>=0.1.0,<1.0.0`` (list of comma-separated constraints)
- ``1.x`` or ``1.2.x`` (wildcard)
- ``1.0.0`` (exact) and ``~=1.2.0`` (compatible-release, PEP 440 flavour)
- ``>=5.0.0``, ``<2.0.0``
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"(?:\.(?P<minor>0|[1-9]\d*))?"
    r"(?:\.(?P<patch>0|[1-9]\d*))?"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)

_RANGE_PART_RE = re.compile(r"^\s*(>=|<=|>|<|==|~=|=)?\s*([^\s,]+)\s*$")


@dataclass(frozen=True)
class Version:
    """A parsed semantic version.

    Prerelease identifiers rank lower than a release of the same
    major/minor/patch; build metadata is ignored for ordering.
    """

    major: int
    minor: int = 0
    patch: int = 0
    prerelease: str | None = None
    build: str | None = None

    def __lt__(self, other: Version) -> bool:
        return compare(self, other) < 0

    def __le__(self, other: Version) -> bool:
        return compare(self, other) <= 0

    def __gt__(self, other: Version) -> bool:
        return compare(self, other) > 0

    def __ge__(self, other: Version) -> bool:
        return compare(self, other) >= 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return compare(self, other) == 0

    def __hash__(self) -> int:
        # Hash ignores build metadata so equal versions hash alike.
        return hash((self.major, self.minor, self.patch, self.prerelease))

    @classmethod
    def parse(cls, text: str) -> Version:
        match = _VERSION_RE.match(text.strip())
        if match is None:
            raise ValueError(f"invalid semantic version: {text!r}")
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor") or 0),
            patch=int(match.group("patch") or 0),
            prerelease=match.group("prerelease"),
            build=match.group("build"),
        )

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            base += f"-{self.prerelease}"
        if self.build:
            base += f"+{self.build}"
        return base


def _prerelease_key(v: Version) -> tuple[int, tuple[int | str, ...]]:
    """Return a sortable tuple that places the release above any prerelease."""
    if v.prerelease is None:
        return (1, ())  # release > any prerelease (higher key wins)
    parts: list[int | str] = []
    for token in v.prerelease.split("."):
        parts.append(int(token) if token.isdigit() else token)
    # Prerelease identifiers compare lexically; numeric < alphanumeric.
    return (0, tuple(parts))


def compare(a: Version, b: Version) -> int:
    """Return -1/0/1 comparing *a* and *b* (build metadata ignored)."""
    for lhs, rhs in ((a.major, b.major), (a.minor, b.minor), (a.patch, b.patch)):
        if lhs != rhs:
            return -1 if lhs < rhs else 1
    ka, kb = _prerelease_key(a), _prerelease_key(b)
    if ka[0] != kb[0]:
        return -1 if ka[0] < kb[0] else 1
    return (ka[1] > kb[1]) - (ka[1] < kb[1])


def satisfies(version: Version, constraint: str) -> bool:
    """Return whether *version* satisfies a single constraint ``str``.

    The constraint may be a bare ``1.x``/``1.2.3`` or an operator form like
    ``>=1.0.0``. Comma-separated ranges are handled by :func:`satisfies_range`.
    """
    raw = constraint.strip()
    if not raw:
        return True

    # Wildcard form "1.x" / "1.2.x".
    if raw.endswith(".x") or raw.endswith(".*"):
        parts = raw[:-2].split(".")
        numbers = [int(p) for p in parts if p.isdigit()]
        if len(numbers) >= 1 and version.major != numbers[0]:
            return False
        return len(numbers) < 2 or version.minor == numbers[1]

    match = _RANGE_PART_RE.match(raw)
    if match is None:
        # Unrecognised — treat as exact parse and compare accordingly.
        return compare(version, Version.parse(raw)) == 0
    op, operand = match.group(1), match.group(2)
    # Operand may itself be a wildcard (e.g. ">=1.x").
    if operand.endswith(".x") or operand.endswith(".*"):
        return satisfies(version, operand)
    target = Version.parse(operand)

    if op in (None, "*", "") or op == "=" or op == "==":
        return compare(version, target) == 0
    if op == ">":
        return compare(version, target) == 1
    if op == ">=":
        return compare(version, target) >= 0
    if op == "<":
        return compare(version, target) == -1
    if op == "<=":
        return compare(version, target) <= 0
    if op == "~=":  # compatible-release: >=X.Y.Z, <X.(Y+1).*
        return compare(version, target) >= 0 and (
            version.major < target.major
            or (version.major == target.major and version.minor < target.minor + 1)
        )
    raise ValueError(f"unsupported constraint operator: {op!r}")


def satisfies_range(version: Version, range_spec: str) -> bool:
    """Return whether *version* satisfies a comma-separated *range_spec*.

    An empty or ``*`` spec matches everything.
    """
    spec = range_spec.strip()
    if not spec or spec == "*":
        return True
    for part in spec.split(","):
        part = part.strip()
        if part and not satisfies(version, part):
            return False
    return True


def best_match(available: list[str], range_spec: str) -> str | None:
    """Return the newest available version satisfying *range_spec* (or None)."""
    candidates = [
        Version.parse(v)
        for v in available
        if satisfies_range(Version.parse(v), range_spec)
    ]
    if not candidates:
        return None
    return str(max(candidates, key=lambda v: (v.major, v.minor, v.patch)))


# ---------------------------------------------------------------------------
# Core compatibility (derived from the installed package, informative).
# ---------------------------------------------------------------------------

from book2skill import __version__  # noqa: E402

CORE_VERSION = __version__
"""The Core version used to validate extension compatibility ranges."""
