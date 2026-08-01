"""Build the official ``book2skill-core-<semver>.zip`` release package.

Usage from the repository root::

    python scripts/build_release.py --dest dist/release
    python scripts/build_release.py --wheel dist/book2skill-0.1.0-py3-none-any.whl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from book2skill.packaging import ReleaseError, build_release


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Book2Skill Core release ZIP."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(".").resolve(),
        help="Repository root providing schemas, docs and templates.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path("dist/release"),
        help="Directory to write release ZIP + .sha256.",
    )
    parser.add_argument(
        "--version", type=str, default=None, help="Override version tag."
    )
    parser.add_argument(
        "--wheel",
        type=Path,
        default=None,
        help="Built wheel to include under dist/ (e.g. dist/*.whl).",
    )
    parser.add_argument(
        "--skill-dir",
        type=Path,
        default=Path("skills/book2skill"),
        help="Meta-Skill directory to package under skills/.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        zip_path = build_release(
            repo_root=args.repo_root,
            dest_dir=args.dest,
            version=args.version,
            wheel=args.wheel,
            skill_dir=(
                args.skill_dir if (args.skill_dir and args.skill_dir.exists()) else None
            ),
        )
    except ReleaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"release built: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
