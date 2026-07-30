# SPDX-License-Identifier: MIT
#
# Selective port from virgiliojr94/book-to-skill
#   Original file: book_to_skill/sanitize.py
#   Repository: https://github.com/virgiliojr94/book-to-skill
#   Commit: 92b248fa5e7039d770d56630444310e36ff014e0
#   License: MIT (see LICENSES/MIT-upstream-virgilio-book-to-skill.txt)
#   Provenance ID: upstream-virgilio-book-to-skill
#
# Local modifications:
#   - Added provenance header.
#   - No functional changes.

"""Remove invisible code points used for document-borne prompt injection."""

from __future__ import annotations

_ZERO_WIDTH_CODEPOINTS = frozenset({0x200B, 0x200C, 0x200D, 0xFEFF})
_TAG_BLOCK_START = 0xE0000
_TAG_BLOCK_END = 0xE007F


def sanitize_extracted_text(text: str) -> tuple[str, int]:
    """Remove invisible code points used for document-borne prompt injection."""
    kept: list[str] = []
    removed = 0

    for character in text:
        codepoint = ord(character)
        if (
            codepoint in _ZERO_WIDTH_CODEPOINTS
            or _TAG_BLOCK_START <= codepoint <= _TAG_BLOCK_END
        ):
            removed += 1
            continue
        kept.append(character)

    return "".join(kept), removed
