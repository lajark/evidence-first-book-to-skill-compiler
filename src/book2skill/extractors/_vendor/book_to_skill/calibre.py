# SPDX-License-Identifier: MIT
#
# Selective port from virgiliojr94/book-to-skill
#   Original file: book_to_skill/parsers/calibre.py
#   Repository: https://github.com/virgiliojr94/book-to-skill
#   Commit: 92b248fa5e7039d770d56630444310e36ff014e0
#   License: MIT (see LICENSES/MIT-upstream-virgilio-book-to-skill.txt)
#   Provenance ID: upstream-virgilio-book-to-skill
#
# Local modifications:
#   - Added provenance header.
#   - Replaced the upstream ``book_to_skill.config.OUTPUT_DIR`` dependency with
#     a per-call tempfile, so the function is self-contained and does not
#     require a project-level output directory.
#   - Isolated Calibre's configuration under the per-call temporary directory
#     and forced its subprocess scratch files into the same writable tree.
#   - Made subprocess output decoding deterministic on non-UTF-8 hosts.

"""MOBI/AZW text extraction via the Calibre ``ebook-convert`` CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_with_ebook_convert(input_path: str) -> str | None:
    if not shutil.which("ebook-convert"):
        return None
    # Use a per-call temp file so the function is self-contained and does not
    # depend on a project-level output directory (upstream used OUTPUT_DIR).
    tmp_dir = Path(tempfile.mkdtemp(prefix="b2s-ebook-convert-"))
    output_path = tmp_dir / "ebook-convert-output.txt"
    try:
        input_path = os.path.abspath(input_path)
        env = os.environ.copy()
        env["CALIBRE_CONFIG_DIRECTORY"] = str(tmp_dir / "calibre-config")
        calibre_temp = tmp_dir / "calibre-temp"
        calibre_temp.mkdir()
        # Calibre creates its own nested temporary workspace. Explicitly route
        # all common tempfile variables so a restricted host cannot send that
        # child process back to an unwritable system-wide Temp directory.
        for variable in ("TMPDIR", "TEMP", "TMP"):
            env[variable] = str(calibre_temp)
        result = subprocess.run(
            ["ebook-convert", input_path, str(output_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )
        if result.returncode == 0 and output_path.exists():
            text = output_path.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                return text
    except Exception as e:
        print(
            f"  [warn] extract_with_ebook_convert failed: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
    finally:
        # Best-effort cleanup of the temp dir and any Calibre sidecar files.
        import contextlib

        with contextlib.suppress(Exception):
            shutil.rmtree(tmp_dir, ignore_errors=True)
    return None
