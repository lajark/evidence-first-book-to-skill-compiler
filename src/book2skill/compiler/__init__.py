"""Compiler layer: SkillIR construction and Skill directory generation.

The compiler is the host-agnostic core of the pipeline (ARCHITECTURE §3
steps 6–7). It transforms reviewed :class:`~book2skill.domain.KnowledgeUnit`
records into a :class:`SkillIR`, then renders a standard Skill directory.

Public surface:

- :class:`SkillIR`, :class:`SkillSpec`, :class:`IRBuilder` — build the IR
  from knowledge units (pure, no I/O).
- :class:`SkillWriter` — write the IR to a Skill directory on disk.
- :class:`TokenBudget`, :func:`estimate_tokens`, :func:`check_budget` —
  token-budget estimation and gating.
- :func:`validate_skill_ir_against_schema` — JSON Schema conformance check
  for the test suite.
"""

from __future__ import annotations

from book2skill.compiler.ir_builder import (
    IRBuilder,
    SkillIR,
    SkillSpec,
    SkillUsage,
    WorkflowStep,
    validate_skill_ir_against_schema,
)
from book2skill.compiler.skill_writer import SkillWriter
from book2skill.compiler.token_budget import (
    BudgetResult,
    TokenBudget,
    check_budget,
    estimate_tokens,
)
from book2skill.compiler.wiki_generator import WikiGenerator

__all__ = [
    "SkillIR",
    "SkillSpec",
    "SkillUsage",
    "WorkflowStep",
    "IRBuilder",
    "SkillWriter",
    "TokenBudget",
    "BudgetResult",
    "estimate_tokens",
    "check_budget",
    "validate_skill_ir_against_schema",
    "WikiGenerator",
]
