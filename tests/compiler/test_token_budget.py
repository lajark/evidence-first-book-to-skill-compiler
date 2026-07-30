"""Tests for the heuristic token-budget estimator."""

from __future__ import annotations

import pytest

from book2skill.compiler.token_budget import (
    BudgetResult,
    TokenBudget,
    check_budget,
    estimate_tokens,
)


class TestEstimateTokens:
    def test_empty_returns_zero(self) -> None:
        assert estimate_tokens("") == 0

    def test_english_approximately_chars_over_4(self) -> None:
        # 40 ASCII chars -> ~10 tokens.
        text = "a" * 40
        assert estimate_tokens(text) == 10

    def test_cjk_uses_tighter_ratio(self) -> None:
        # 15 CJK chars / 1.5 = 10 tokens.
        text = "字" * 15
        assert estimate_tokens(text) == 10

    def test_mixed_sums_components(self) -> None:
        # 12 CJK (8 tokens) + 16 ASCII (4 tokens) = 12 tokens.
        text = "字" * 12 + "a" * 16
        assert estimate_tokens(text) == 12

    def test_rounds_up(self) -> None:
        # 5 ASCII chars -> ceil(5/4) = 2 tokens.
        assert estimate_tokens("hello") == 2

    def test_whitespace_counted_as_other(self) -> None:
        # 8 spaces -> ceil(8/4) = 2 tokens.
        assert estimate_tokens("        ") == 2


class TestTokenBudget:
    def test_defaults_match_prd(self) -> None:
        b = TokenBudget()
        assert b.target_min == 2500
        assert b.target_max == 5000
        assert b.hard_max == 5000

    def test_min_greater_than_max_raises(self) -> None:
        with pytest.raises(ValueError, match="target_min"):
            TokenBudget(target_min=6000, target_max=5000)

    def test_hard_max_below_target_max_raises(self) -> None:
        with pytest.raises(ValueError, match="hard_max"):
            TokenBudget(target_max=5000, hard_max=4000)

    def test_custom_hard_max_with_grace_margin(self) -> None:
        b = TokenBudget(target_max=5000, hard_max=6000)
        assert b.hard_max == 6000


class TestCheckBudget:
    def test_within_target(self) -> None:
        # 2000 ASCII chars -> 500 tokens; bump to ~3000 chars for 750... use
        # a budget that brackets 500.
        text = "a" * 2000
        result = check_budget(text, TokenBudget(target_min=400, target_max=600))
        assert isinstance(result, BudgetResult)
        assert result.tokens == 500
        assert result.within_target
        assert not result.below_target_min
        assert not result.exceeds_hard_max
        assert result.within_hard_max

    def test_below_target_min_flagged_but_valid(self) -> None:
        result = check_budget("hi", TokenBudget(target_min=100, target_max=500))
        assert result.below_target_min
        assert not result.within_target
        assert not result.exceeds_hard_max

    def test_exceeds_hard_max(self) -> None:
        text = "a" * 100_000  # 25000 tokens
        result = check_budget(text, TokenBudget(hard_max=5000))
        assert result.exceeds_hard_max
        assert not result.within_hard_max
        assert not result.within_target
