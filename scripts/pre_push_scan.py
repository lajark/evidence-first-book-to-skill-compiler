"""Pre-push sensitive-data scanner for Book2Skill.

Runs the checks documented in GITEE_PRIVATE_REPO.md §4 before the first
Gitee push. Exits non-zero if any tracked file matches a sensitive path
pattern, so it can be wired into a pre-push hook or run manually.

Usage:
    python scripts/pre_push_scan.py
    python scripts/pre_push_scan.py --staged     # scan staged files only
    python scripts/pre_push_scan.py --untracked  # scan untracked files too

This script is read-only: it does not modify, delete, or stage any file.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Patterns that must NEVER be tracked. See GITEE_PRIVATE_REPO.md and .gitignore.
SENSITIVE_PATTERNS = [
    ".env",
    ".env.*",
    "secrets/**",
    "config.local.*",
    "CLAUDE.local.md",
    # Copyright / personal data
    "workspace/**",
    "library/raw/**",
    "library/books/**",
    "*.pdf",
    "*.epub",
    "*.mobi",
    "*.azw",
    "*.azw3",
    # Credentials / keys
    "*.pem",
    "*.key",
    "*id_ed25519*",
    # Build / cache artifacts
    ".coverage",
    "htmlcov/**",
    "coverage.xml",
    "workspace/acceptance/**",
]

# Safe, intentionally tracked templates.  ``.env.*`` remains blocked for all
# other files so a real local environment file cannot enter the repository.
ALLOWED_TEMPLATE_PATHS = {".env.example"}

# Patterns that look sensitive in file content.  Match complete, sufficiently
# long token-like values rather than prefixes in documentation and tests.
CONTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(r"-----BEGIN (?:OPENSSH )?PRIVATE KEY-----")),
    (
        "openai-key",
        re.compile(
            r"\bsk-(?=[A-Za-z0-9_-]{20,}\b)"
            r"(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b"
        ),
    ),
    ("github-pat", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("github-oauth", re.compile(r"\bgho_[A-Za-z0-9]{30,}\b")),
]
_SYNTHETIC_TOKEN_MARKERS = (
    "example",
    "dummy",
    "fake",
    "local",
    "placeholder",
    "test",
    "your-key",
)


def _match_any(path_str: str, patterns: list[str]) -> str | None:
    if path_str.replace("\\", "/") in ALLOWED_TEMPLATE_PATHS:
        return None
    for pat in patterns:
        if fnmatch.fnmatch(path_str, pat) or fnmatch.fnmatch(
            path_str, "**/" + pat
        ):
            return pat
    return None


def _git_list_files(extra_args: list[str]) -> list[str]:
    import subprocess

    cmd = ["git", "ls-files", *extra_args]
    proc = subprocess.run(
        cmd,
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(
            f"WARN: `{' '.join(cmd)}` failed (exit={proc.returncode}): "
            f"{proc.stderr.strip()}",
            file=sys.stderr,
        )
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _scan_paths(files: list[str]) -> list[tuple[str, str]]:
    """Return [(path, matched_pattern), ...] for sensitive matches."""
    hits: list[tuple[str, str]] = []
    for f in files:
        # Normalize to forward slashes for fnmatch.
        norm = f.replace("\\", "/")
        pat = _match_any(norm, SENSITIVE_PATTERNS)
        if pat:
            hits.append((f, pat))
    return hits


def _scan_content(files: list[str]) -> list[tuple[str, str]]:
    """Scan file contents for credential markers. Text files only."""
    hits: list[tuple[str, str]] = []
    for f in files:
        # This scanner necessarily contains its own marker patterns; scanning
        # it would make every clean repository fail closed on its own code.
        if f.replace("\\", "/") == "scripts/pre_push_scan.py":
            continue
        path = _REPO_ROOT / f
        if not path.is_file():
            continue
        # Skip obviously binary extensions.
        if path.suffix.lower() in {".pdf", ".epub", ".mobi", ".azw",
                                    ".azw3", ".png", ".jpg", ".jpeg",
                                    ".pyc", ".db"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in CONTENT_PATTERNS:
            match = pattern.search(text)
            if match and not any(
                marker in match.group(0).casefold()
                for marker in _SYNTHETIC_TOKEN_MARKERS
            ):
                hits.append((f, f"content:{label}"))
                break
    return hits


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged", action="store_true",
        help="Scan staged files (git diff --cached --name-only) instead of tracked.",
    )
    parser.add_argument(
        "--untracked", action="store_true",
        help="Also scan untracked files (git ls-files --others).",
    )
    args = parser.parse_args(argv)

    # First: is this even a git repo?
    import subprocess

    is_repo = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if is_repo.returncode != 0:
        print(
            "INFO: not a git repository yet. Run `git init` first, then re-run.",
            file=sys.stderr,
        )
        return 2

    files: list[str] = []
    if args.staged:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        files = [line for line in proc.stdout.splitlines() if line.strip()]
    else:
        files = _git_list_files([])
        if args.untracked:
            files.extend(_git_list_files(["--others", "--exclude-standard"]))

    if not files:
        print("No files to scan.")
        return 0

    path_hits = _scan_paths(files)
    content_hits = _scan_content(files)

    if not path_hits and not content_hits:
        print(f"OK: scanned {len(files)} file(s); no sensitive matches.")
        return 0

    print(f"FAIL: sensitive data detected in {len(files)} scanned file(s):",
          file=sys.stderr)
    for path, pat in path_hits:
        print(f"  PATH  {path}  (matched: {pat})", file=sys.stderr)
    for path, pat in content_hits:
        print(f"  CONTENT  {path}  (matched: {pat})", file=sys.stderr)
    print(
        "\nRemediation:\n"
        "  1. If the file should be ignored: add it to .gitignore.\n"
        "  2. If already tracked: `git rm --cached <path>` (keeps the local file).\n"
        "  3. If a secret leaked into history: rotate it immediately, then use\n"
        "     `git filter-repo` to rewrite history before pushing.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
