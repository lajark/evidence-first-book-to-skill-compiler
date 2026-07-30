"""Token-budget estimation for generated Skills.

The compiler must keep the main ``SKILL.md`` within the PRD FR-04 target
(2,500–5,000 tokens). Because the project defaults to offline operation and
must not pin a specific host tokenizer, :func:`estimate_tokens` uses a
deterministic heuristic instead of pulling in ``tiktoken``:

- CJK ideographs (Han/Hiragana/Katakana/Hangul) contribute ~1.5 chars/token,
  matching the empirical ratio of common BPE tokenizers on Chinese text.
- Latin/whitespace/punctuation text contributes ~4 chars/token, the standard
  OpenAI rule of thumb for English.

The estimate is intentionally conservative (rounds up) so a Skill that passes
the budget gate here is very unlikely to be rejected by a stricter host
validator. Per risk-register item V-02, the heuristic is calibrated against the
official host validator in M5; the estimator interface is simple enough to swap
for ``tiktoken`` behind the same function signature when that lands.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass


def _is_cjk(char: str) -> bool:
    """True if *char* is a CJK ideograph or kana likely to be 1 token per ~1.5 chars."""
    if not char:
        return False
    code = ord(char)
    # CJK Unified Ideographs + extensions A/B (common), Hiragana, Katakana,
    # Hangul Syllables, CJK punctuation and fullwidth forms.
    return (
        0x4E00 <= code <= 0x9FFF        # CJK Unified Ideographs
        or 0x3400 <= code <= 0x4DBF     # CJK Extension A
        or 0x3040 <= code <= 0x30FF     # Hiragana + Katakana
        or 0xAC00 <= code <= 0xD7AF     # Hangul Syllables
        or 0x3000 <= code <= 0x303F     # CJK Symbols and Punctuation
        or 0xFF00 <= code <= 0xFFEF     # Halfwidth and Fullwidth Forms
    )


def estimate_tokens(text: str) -> int:
    """Estimate the token count of *text* using a deterministic heuristic.

    CJK characters use ~1.5 chars/token; all other characters use ~4
    chars/token. The result is rounded up so the budget gate errs on the
    safe side. Returns 0 for empty text.
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        # Normalize away combining marks so they don't inflate counts.
        if unicodedata.combining(ch):
            continue
        if _is_cjk(ch):
            cjk += 1
        else:
            other += 1
    tokens = math.ceil(cjk / 1.5) + math.ceil(other / 4.0)
    return tokens


@dataclass(frozen=True)
class TokenBudget:
    """Token-budget thresholds for the main Skill file.

    ``target_min``/``target_max`` define the desired PRD FR-04 range.
    ``hard_max`` is the absolute ceiling: exceeding it raises
    :class:`~book2skill.domain.ErrorCode.BUILD_BUDGET_EXCEEDED` during
    compilation. By default ``hard_max`` equals ``target_max``; callers that
    want a grace margin (e.g. hard_max=6000) can pass it explicitly.
    """

    target_min: int = 2500
    target_max: int = 5000
    hard_max: int = 5000

    def __post_init__(self) -> None:
        if self.target_min < 0 or self.target_max <= 0:
            raise ValueError("token budget thresholds must be positive")
        if self.target_min > self.target_max:
            raise ValueError("target_min must not exceed target_max")
        if self.hard_max < self.target_max:
            raise ValueError("hard_max must be >= target_max")


@dataclass(frozen=True)
class BudgetResult:
    """Outcome of checking a text against a :class:`TokenBudget`."""

    tokens: int
    within_target: bool
    below_target_min: bool
    exceeds_hard_max: bool

    @property
    def within_hard_max(self) -> bool:
        """True when the text fits under the absolute ceiling."""
        return not self.exceeds_hard_max


def check_budget(text: str, budget: TokenBudget) -> BudgetResult:
    """Check *text* against *budget* and return a :class:`BudgetResult`.

    A text below ``target_min`` is valid (a small Skill is still a Skill) but
    flagged via ``below_target_min`` so the compiler can warn. A text above
    ``hard_max`` is rejected by the writer.
    """
    tokens = estimate_tokens(text)
    return BudgetResult(
        tokens=tokens,
        within_target=budget.target_min <= tokens <= budget.target_max,
        below_target_min=tokens < budget.target_min,
        exceeds_hard_max=tokens > budget.hard_max,
    )


__all__ = [
    "TokenBudget",
    "BudgetResult",
    "estimate_tokens",
    "check_budget",
]
