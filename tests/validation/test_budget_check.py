"""Tests for :mod:`book2skill.validation.budget_check`."""

from __future__ import annotations

from pathlib import Path

from book2skill.compiler.token_budget import TokenBudget
from book2skill.validation.budget_check import BudgetCheck
from book2skill.validation.models import CheckStatus

_VALID_FRONTMATTER = (
    "---\n"
    "name: my-skill\n"
    "description: A valid skill that does something useful.\n"
    "---\n"
)


def _write_skill_md(skill_dir: Path, body: str) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _VALID_FRONTMATTER + body, encoding="utf-8"
    )
    return skill_dir


class TestBudgetCheck:
    def test_within_target_passes(self, tmp_path: Path) -> None:
        # ~3000 tokens of Latin text (≈ 12000 chars / 4).
        body = " ".join(["word"] * 3000) + "\n"
        _write_skill_md(tmp_path, body)
        result = BudgetCheck().run(tmp_path)
        assert result.status == CheckStatus.PASS

    def test_exceeds_hard_max_fails(self, tmp_path: Path) -> None:
        # Use a tight budget so the test stays fast.
        body = " ".join(["word"] * 100) + "\n"
        _write_skill_md(tmp_path, body)
        budget = TokenBudget(target_min=10, target_max=20, hard_max=20)
        check = BudgetCheck(budget=budget)
        result = check.run(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert "budget.exceeds_hard_max" in result.evidence

    def test_below_target_min_warns(self, tmp_path: Path) -> None:
        body = "# Skill\n\nShort.\n"
        _write_skill_md(tmp_path, body)
        budget = TokenBudget(target_min=1000, target_max=5000, hard_max=10000)
        check = BudgetCheck(budget=budget)
        result = check.run(tmp_path)
        assert result.status == CheckStatus.WARN
        assert "budget.below_target_min" in result.evidence

    def test_empty_skill_md_warns_below_min(self, tmp_path: Path) -> None:
        _write_skill_md(tmp_path, "")
        result = BudgetCheck().run(tmp_path)
        assert result.status == CheckStatus.WARN
        assert "budget.below_target_min" in result.evidence
