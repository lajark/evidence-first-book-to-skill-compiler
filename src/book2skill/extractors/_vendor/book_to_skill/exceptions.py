# SPDX-License-Identifier: MIT
#
# Selective port from virgiliojr94/book-to-skill
#   Original file: book_to_skill/exceptions.py
#   Repository: https://github.com/virgiliojr94/book-to-skill
#   Commit: 92b248fa5e7039d770d56630444310e36ff014e0
#   License: MIT (see LICENSES/MIT-upstream-virgilio-book-to-skill.txt)
#   Provenance ID: upstream-virgilio-book-to-skill
#
# Local modifications:
#   - Added provenance header.
#   - No functional changes.

"""Extraction failure exception (non-fatal in batch mode)."""


class ExtractionError(Exception):
    """Raised when a single file cannot be extracted (non-fatal in batch mode)."""
