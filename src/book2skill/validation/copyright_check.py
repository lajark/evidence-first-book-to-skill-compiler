"""Long-quote / copyright check (PRD FR-08, SECURITY.md).

PRD FR-08 caps direct quotations at "25 English words or equivalent Chinese
characters". This check scans ``references/*.md`` for source quotes emitted
by :func:`~book2skill.compiler.ir_builder.IRBuilder._render_reference`:

    **Sources:**
    - <source_id> / <block_id> — "<quote>"

and measures each quote's effective word count. The count mixes English words
(split on whitespace) and Chinese characters (CJK range, reused from
:mod:`~book2skill.compiler.token_budget`); Chinese characters are scaled by
the same 1.5 chars/token ratio used by the token-budget estimator so the two
heuristics stay consistent (verification item V-03).

Behaviour:

- effective_words > 40 → ``fail`` (clear copyright risk).
- 25 < effective_words <= 40 → ``warn`` (``copyright.long_quote``).
- effective_words <= 25 → no finding (within the PRD cap).
- Quotes missing entirely are skipped (a quote is optional on a KnowledgeRef).

The 25/40 thresholds are configurable via ``max_quote_words`` /
``hard_max_quote_words`` so a host with a stricter policy can lower them.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from book2skill.compiler.token_budget import _is_cjk
from book2skill.validation.models import BaseCheck, CheckStatus, Finding

#: Default soft cap (PRD FR-08: 25 English words or equivalent Chinese).
_DEFAULT_MAX_QUOTE_WORDS = 25

#: Hard cap above which the quote is treated as a copyright violation.
_DEFAULT_HARD_MAX_QUOTE_WORDS = 40

#: Match a source citation line that carries a quoted excerpt. The quote is
#: everything between the ``— "`` and the trailing ``"``. Mirrors the format
#: emitted by :func:`~book2skill.compiler.ir_builder.IRBuilder._render_reference`.
_QUOTE_LINE_RE = re.compile(
    r'^\s*-\s+(?P<source_id>[^\s/]+)\s*/\s*(?P<block_id>[^\s]+)'
    r'\s+—\s+"(?P<quote>.*)"\s*$',
    re.MULTILINE,
)

#: A Markdown section header ``## <unit_id>`` inside references/*.md.
_SECTION_HEADER_RE = re.compile(r"^##\s+(?P<unit_id>.+?)\s*$", re.MULTILINE)


class CopyrightCheck(BaseCheck):
    """Flag direct quotations that exceed the PRD FR-08 word cap."""

    check_id = "copyright"

    def __init__(
        self,
        *,
        max_quote_words: int = _DEFAULT_MAX_QUOTE_WORDS,
        hard_max_quote_words: int = _DEFAULT_HARD_MAX_QUOTE_WORDS,
    ) -> None:
        if max_quote_words <= 0 or hard_max_quote_words <= 0:
            raise ValueError("quote thresholds must be positive")
        if max_quote_words > hard_max_quote_words:
            raise ValueError("max_quote_words must not exceed hard_max_quote_words")
        self._max = max_quote_words
        self._hard_max = hard_max_quote_words

    def _run(self, skill_dir: Path) -> list[Finding]:
        findings: list[Finding] = []
        references_dir = skill_dir / "references"
        if not references_dir.is_dir():
            # No references → no quotes to check; coverage of this case is
            # already reported by SourceCheck.
            return findings

        for ref_file in sorted(references_dir.glob("*.md")):
            rel = ref_file.relative_to(skill_dir).as_posix()
            text = ref_file.read_text(encoding="utf-8")
            self._check_file(text, rel, findings)
        return findings

    def _check_file(
        self, text: str, rel_path: str, findings: list[Finding]
    ) -> None:
        """Inspect every quoted citation in a single reference file."""
        # Map line offsets so we can report ``<file>:<line>`` locations.
        line_starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                line_starts.append(i + 1)

        for match in _QUOTE_LINE_RE.finditer(text):
            quote = match.group("quote")
            if not quote:
                continue
            words = self._effective_words(quote)
            line_no = self._line_for_offset(match.start(), line_starts)
            location = f"{rel_path}:{line_no}"

            if words > self._hard_max:
                findings.append(
                    Finding(
                        severity=CheckStatus.FAIL,
                        code="copyright.quote_too_long",
                        location=location,
                        message=(
                            f"Direct quote is {words} effective words "
                            f"(hard cap {self._hard_max}); this exceeds the "
                            "PRD FR-08 copyright ceiling. Shorten or "
                            "paraphrase."
                        ),
                    )
                )
            elif words > self._max:
                findings.append(
                    Finding(
                        severity=CheckStatus.WARN,
                        code="copyright.long_quote",
                        location=location,
                        message=(
                            f"Direct quote is {words} effective words "
                            f"(cap {self._max}); review for copyright "
                            "compliance or split into shorter excerpts."
                        ),
                    )
                )

    @staticmethod
    def _effective_words(text: str) -> int:
        """Estimate the effective word count of a quotation.

        English words split on whitespace plus Chinese characters scaled by
        1.5 chars/word (consistent with the token-budget heuristic). Returns
        0 for empty text.
        """
        if not text:
            return 0
        cjk = 0
        for ch in text:
            if _is_cjk(ch):
                cjk += 1
        # Strip CJK characters before splitting so they don't inflate the
        # English word count.
        latin = "".join(ch for ch in text if not _is_cjk(ch))
        latin_words = len(latin.split())
        return latin_words + math.ceil(cjk / 1.5)

    @staticmethod
    def _line_for_offset(offset: int, line_starts: list[int]) -> int:
        """Return the 1-based line number for a character offset."""
        # Binary search would be overkill for typical reference sizes.
        import bisect

        return bisect.bisect_right(line_starts, offset)


__all__ = ["CopyrightCheck"]
