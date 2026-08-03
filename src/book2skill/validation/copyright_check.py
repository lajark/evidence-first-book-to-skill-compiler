"""Long-quote / copyright check (PRD FR-08, SECURITY.md).

PRD FR-08 caps direct quotations at "25 English words or equivalent Chinese
characters". This check scans every generated Markdown body (except its own
quality report) for source quotes emitted by
:func:`~book2skill.compiler.ir_builder.IRBuilder._render_reference`:

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
- Same-source excerpts with a high overlap of three-token fingerprints are
  also aggregated. This catches sentence-order changes that a sequence-only
  similarity check can miss, without requiring a network model or retaining
  source text outside the generated artifact.

The 25/40 thresholds are configurable via ``max_quote_words`` /
``hard_max_quote_words`` so a host with a stricter policy can lower them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
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
_BLOCK_NUMBER_RE = re.compile(r"(?:^|[-_])(?:p|pg|b)?(?P<number>\d+)$")
_FINGERPRINT_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u3400-\u9fff]")
_FINGERPRINT_SHINGLE_SIZE = 3
_FINGERPRINT_OVERLAP_THRESHOLD = 0.80


@dataclass(frozen=True)
class _QuoteRecord:
    source_id: str
    block_id: str
    quote: str
    words: int
    location: str


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
        quotes: list[_QuoteRecord] = []
        for markdown_file in sorted(skill_dir.rglob("*.md")):
            # Validator writes quality-report.md after checks run. Excluding
            # it also prevents a diagnostic copy of a finding from becoming
            # a new quote candidate on a later validation pass.
            if markdown_file.name == "quality-report.md":
                continue
            rel = markdown_file.relative_to(skill_dir).as_posix()
            text = markdown_file.read_text(encoding="utf-8")
            quotes.extend(self._check_file(text, rel, findings))
        self._check_contiguous_aggregates(quotes, findings)
        self._check_duplicate_quotes(quotes, findings)
        sequence_matches = self._check_similar_quotes(quotes, findings)
        self._check_fingerprint_similar_quotes(
            quotes, findings, sequence_matches=sequence_matches
        )
        return findings

    def _check_file(
        self, text: str, rel_path: str, findings: list[Finding]
    ) -> list[_QuoteRecord]:
        """Inspect every quoted citation in a single reference file."""
        records: list[_QuoteRecord] = []
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
            records.append(
                _QuoteRecord(
                    source_id=match.group("source_id"),
                    block_id=match.group("block_id"),
                    quote=quote,
                    words=words,
                    location=location,
                )
            )

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
        return records

    def _check_contiguous_aggregates(
        self, quotes: list[_QuoteRecord], findings: list[Finding]
    ) -> None:
        """Flag adjacent source blocks that collectively exceed the cap.

        A long excerpt can be split across reference files or individual
        source blocks to evade the per-line check. We only aggregate blocks
        with an explicit numeric sequence (for example ``source-p1`` and
        ``source-p2``), avoiding false positives for unrelated excerpts from
        the same source.
        """
        by_source: dict[str, list[tuple[int, _QuoteRecord]]] = {}
        for quote in quotes:
            match = _BLOCK_NUMBER_RE.search(quote.block_id)
            if match is None:
                continue
            by_source.setdefault(quote.source_id, []).append(
                (int(match.group("number")), quote)
            )

        for records in by_source.values():
            records.sort(key=lambda item: item[0])
            run: list[_QuoteRecord] = []
            previous_number: int | None = None
            for number, quote in records:
                if previous_number is None or number == previous_number + 1:
                    run.append(quote)
                else:
                    self._emit_aggregate(run, findings)
                    run = [quote]
                previous_number = number
            self._emit_aggregate(run, findings)

    def _check_duplicate_quotes(
        self, quotes: list[_QuoteRecord], findings: list[Finding]
    ) -> None:
        """Flag repeated excerpts from the same source across references."""
        groups: dict[tuple[str, str], list[_QuoteRecord]] = {}
        for quote in quotes:
            normalized = re.sub(r"\W+", " ", quote.quote.casefold()).strip()
            if normalized:
                groups.setdefault((quote.source_id, normalized), []).append(quote)

        for records in groups.values():
            if len(records) < 2:
                continue
            words = sum(record.words for record in records)
            if words > self._hard_max:
                findings.append(
                    Finding(
                        severity=CheckStatus.FAIL,
                        code="copyright.duplicate_quote_too_long",
                        location=records[0].location,
                        message=(
                            f"The same source excerpt is repeated {len(records)} "
                            f"times ({words} effective words total; hard cap "
                            f"{self._hard_max}). Remove duplicates or paraphrase."
                        ),
                    )
                )
            elif words > self._max:
                findings.append(
                    Finding(
                        severity=CheckStatus.WARN,
                        code="copyright.duplicate_long_quote",
                        location=records[0].location,
                        message=(
                            f"The same source excerpt is repeated {len(records)} "
                            f"times ({words} effective words total; cap "
                            f"{self._max}); review the combined output."
                        ),
                    )
                )

    def _check_similar_quotes(
        self, quotes: list[_QuoteRecord], findings: list[Finding]
    ) -> set[tuple[str, str]]:
        """Flag near-identical long excerpts from the same source.

        This intentionally uses a high threshold and only compares excerpts
        that are at least half the configured soft cap. It is a conservative
        signal for lightly edited duplicates, not a general plagiarism model.
        """
        candidates = [
            quote for quote in quotes if quote.words >= max(2, self._max // 2)
        ]
        matched_pairs: set[tuple[str, str]] = set()
        for index, left in enumerate(candidates):
            left_text = self._normalize_quote(left.quote)
            for right in candidates[index + 1 :]:
                if left.source_id != right.source_id:
                    continue
                right_text = self._normalize_quote(right.quote)
                if left_text == right_text:
                    continue
                if SequenceMatcher(None, left_text, right_text).ratio() < 0.92:
                    continue
                matched_pairs.add(self._pair_key(left, right))
                words = left.words + right.words
                if words > self._hard_max:
                    findings.append(
                        Finding(
                            severity=CheckStatus.FAIL,
                            code="copyright.similar_quote_too_long",
                            location=left.location,
                            message=(
                                "Two highly similar excerpts from the same "
                                f"source total {words} effective words; "
                                f"hard cap is {self._hard_max}."
                            ),
                        )
                    )
                elif words > self._max:
                    findings.append(
                        Finding(
                            severity=CheckStatus.WARN,
                            code="copyright.similar_long_quote",
                            location=left.location,
                            message=(
                                "Two highly similar excerpts from the same "
                                f"source total {words} effective words; "
                                f"cap is {self._max}."
                            ),
                        )
                    )
        return matched_pairs

    def _check_fingerprint_similar_quotes(
        self,
        quotes: list[_QuoteRecord],
        findings: list[Finding],
        *,
        sequence_matches: set[tuple[str, str]],
    ) -> None:
        """Detect reordered same-source excerpts with indexed phrase overlap.

        The inverted index avoids an all-pairs comparison for large Skills:
        only records sharing a three-token phrase are considered. A high
        Jaccard threshold keeps this a conservative copyright signal rather
        than a broad semantic-plagiarism classifier.
        """
        candidates = [
            quote for quote in quotes if quote.words >= max(2, self._max // 2)
        ]
        fingerprints = [self._fingerprint(quote.quote) for quote in candidates]
        buckets: dict[tuple[str, str], list[int]] = {}
        shared_counts: dict[tuple[int, int], int] = {}

        for index, (quote, fingerprint) in enumerate(
            zip(candidates, fingerprints, strict=True)
        ):
            for shingle in fingerprint:
                bucket = buckets.setdefault((quote.source_id, shingle), [])
                for earlier in bucket:
                    pair = (earlier, index)
                    shared_counts[pair] = shared_counts.get(pair, 0) + 1
                bucket.append(index)

        for (left_index, right_index), shared in sorted(shared_counts.items()):
            left = candidates[left_index]
            right = candidates[right_index]
            pair_key = self._pair_key(left, right)
            if pair_key in sequence_matches:
                continue
            left_text = self._normalize_quote(left.quote)
            right_text = self._normalize_quote(right.quote)
            if left_text == right_text:
                continue

            left_fingerprint = fingerprints[left_index]
            right_fingerprint = fingerprints[right_index]
            smaller = min(len(left_fingerprint), len(right_fingerprint))
            if not smaller or shared / smaller < _FINGERPRINT_OVERLAP_THRESHOLD:
                continue
            overlap = len(left_fingerprint & right_fingerprint) / len(
                left_fingerprint | right_fingerprint
            )
            if overlap < _FINGERPRINT_OVERLAP_THRESHOLD:
                continue

            words = left.words + right.words
            percent = round(overlap * 100)
            if words > self._hard_max:
                findings.append(
                    Finding(
                        severity=CheckStatus.FAIL,
                        code="copyright.fingerprint_similar_quote_too_long",
                        location=left.location,
                        message=(
                            "Two reordered excerpts from the same source share "
                            f"{percent}% of phrase fingerprints and total {words} "
                            f"effective words; hard cap is {self._hard_max}."
                        ),
                    )
                )
            elif words > self._max:
                findings.append(
                    Finding(
                        severity=CheckStatus.WARN,
                        code="copyright.fingerprint_similar_long_quote",
                        location=left.location,
                        message=(
                            "Two reordered excerpts from the same source share "
                            f"{percent}% of phrase fingerprints and total {words} "
                            f"effective words; cap is {self._max}."
                        ),
                    )
                )

    @staticmethod
    def _normalize_quote(text: str) -> str:
        return re.sub(r"\W+", " ", text.casefold()).strip()

    @staticmethod
    def _fingerprint(text: str) -> set[str]:
        """Return order-insensitive three-token phrase fingerprints."""
        tokens = _FINGERPRINT_TOKEN_RE.findall(text.casefold())
        if len(tokens) < _FINGERPRINT_SHINGLE_SIZE:
            return set()
        return {
            "\u241f".join(tokens[index : index + _FINGERPRINT_SHINGLE_SIZE])
            for index in range(len(tokens) - _FINGERPRINT_SHINGLE_SIZE + 1)
        }

    @staticmethod
    def _pair_key(left: _QuoteRecord, right: _QuoteRecord) -> tuple[str, str]:
        first, second = sorted((left.location, right.location))
        return first, second

    def _emit_aggregate(
        self, records: list[_QuoteRecord], findings: list[Finding]
    ) -> None:
        if len(records) < 2:
            return
        words = sum(record.words for record in records)
        if words > self._hard_max:
            findings.append(
                Finding(
                    severity=CheckStatus.FAIL,
                    code="copyright.aggregate_quote_too_long",
                    location=records[0].location,
                    message=(
                        f"Contiguous source excerpts total {words} effective "
                        f"words (hard cap {self._hard_max}); shorten or "
                        "paraphrase the combined passage."
                    ),
                )
            )
        elif words > self._max:
            findings.append(
                Finding(
                    severity=CheckStatus.WARN,
                    code="copyright.aggregate_long_quote",
                    location=records[0].location,
                    message=(
                        f"Contiguous source excerpts total {words} effective "
                        f"words (cap {self._max}); review the combined "
                        "passage for copyright compliance."
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
