# SPDX-License-Identifier: MIT
#
# Selective port from virgiliojr94/book-to-skill
#   Original file: book_to_skill/parsers/text.py
#   Repository: https://github.com/virgiliojr94/book-to-skill
#   Commit: 92b248fa5e7039d770d56630444310e36ff014e0
#   License: MIT (see LICENSES/MIT-upstream-virgilio-book-to-skill.txt)
#   Provenance ID: upstream-virgilio-book-to-skill
#
# Local modifications:
#   - Added provenance header.
#   - Renamed module from `text` to avoid conflict with stdlib `text` in some contexts.
#   - No functional changes.

"""Plain-text file reader with BOM-aware encoding detection."""

from __future__ import annotations

import sys
from pathlib import Path

# Byte-order marks, longest first: the UTF-32 LE BOM ("ff fe 00 00") starts with
# the UTF-16 LE BOM ("ff fe"), so UTF-32 must be checked before UTF-16.
_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)


def read_text_file(path: str) -> str | None:
    """Read a text file, detecting encoding from BOM or falling back heuristically."""
    try:
        data = Path(path).read_bytes()
    except Exception as e:
        print(
            f"  [warn] read_text_file failed: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return None

    # Decode by BOM when present (the utf-16/utf-32 codecs strip the BOM and
    # auto-select byte order).
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                break

    # No (usable) BOM: fall back to the prior chain for BOM-less files.
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None
