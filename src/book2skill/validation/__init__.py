"""Validation stage: security and quality checks for compiled Skills (TASK-016).

Public entry points:

- :class:`Validator` — runs every check against a Skill directory and returns
  a :class:`QualityReport`.
- :class:`QualityReportWriter` — renders the report as Markdown / JSON.
- Individual checks: :class:`FrontmatterCheck`, :class:`SourceCheck`,
  :class:`CopyrightCheck`, :class:`InjectionCheck`, :class:`BudgetCheck`.

All checks are read-only; writing the report is opt-in via the CLI
``--write`` flag and only ever touches ``quality-report.md`` /
``quality-report.json``.
"""

from __future__ import annotations

from book2skill.validation.budget_check import BudgetCheck
from book2skill.validation.copyright_check import CopyrightCheck
from book2skill.validation.frontmatter_check import FrontmatterCheck
from book2skill.validation.injection_check import InjectionCheck
from book2skill.validation.models import (
    BaseCheck,
    CheckResult,
    CheckStatus,
    Finding,
    QualityReport,
    ReportStatus,
)
from book2skill.validation.quality_report import QualityReportWriter, Validator
from book2skill.validation.source_check import SourceCheck

__all__ = [
    "Validator",
    "QualityReportWriter",
    "BaseCheck",
    "CheckResult",
    "CheckStatus",
    "Finding",
    "QualityReport",
    "ReportStatus",
    "FrontmatterCheck",
    "SourceCheck",
    "CopyrightCheck",
    "InjectionCheck",
    "BudgetCheck",
]
