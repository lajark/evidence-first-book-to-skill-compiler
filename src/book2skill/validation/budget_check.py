"""Token-budget check for the main ``SKILL.md`` (PRD FR-07, FR-04).

PRD FR-04 targets 2,500–5,000 tokens for the main Skill file. The compiler
enforces a hard ceiling via
:func:`~book2skill.compiler.skill_writer.SkillWriter._enforce_budget`; this
check re-runs the same estimator against the *on-disk* file so a Skill that
was edited after compilation (or whose budget drifted) is still caught before
publication.

Only ``SKILL.md`` is budgeted; ``references/*.md`` files are intentionally
exempt because the budget is about the agent's primary context, not the
supporting material (see ARCHITECTURE §4 and
:mod:`~book2skill.compiler.token_budget`).
"""

from __future__ import annotations

from pathlib import Path

from book2skill.compiler.token_budget import BudgetResult, TokenBudget, check_budget
from book2skill.validation.models import BaseCheck, CheckStatus, Finding

#: Default budget mirroring :mod:`~book2skill.compiler.skill_writer`.
_DEFAULT_BUDGET = TokenBudget()


class BudgetCheck(BaseCheck):
    """Check the main ``SKILL.md`` against the PRD FR-04 token budget."""

    check_id = "budget"

    def __init__(self, budget: TokenBudget | None = None) -> None:
        self._budget = budget or _DEFAULT_BUDGET

    def _run(self, skill_dir: Path) -> list[Finding]:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            # Structural absence is handled by the Validator; emit a soft
            # finding so the check is not silently skipped.
            return [
                Finding(
                    severity=CheckStatus.FAIL,
                    code="budget.missing_skill_md",
                    location="SKILL.md",
                    message="SKILL.md not found; cannot check token budget.",
                )
            ]

        text = skill_md.read_text(encoding="utf-8")
        result: BudgetResult = check_budget(text, self._budget)
        if result.exceeds_hard_max:
            return [
                Finding(
                    severity=CheckStatus.FAIL,
                    code="budget.exceeds_hard_max",
                    location="SKILL.md",
                    message=(
                        f"SKILL.md is ~{result.tokens} tokens, exceeding the "
                        f"hard ceiling of {self._budget.hard_max}."
                    ),
                )
            ]
        if result.below_target_min:
            return [
                Finding(
                    severity=CheckStatus.WARN,
                    code="budget.below_target_min",
                    location="SKILL.md",
                    message=(
                        f"SKILL.md is ~{result.tokens} tokens, below the "
                        f"target minimum of {self._budget.target_min}."
                    ),
                )
            ]
        return []


__all__ = ["BudgetCheck"]
