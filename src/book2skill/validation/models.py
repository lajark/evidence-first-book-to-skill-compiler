"""Quality-gate data models for the validation stage (TASK-016, PRD FR-07/FR-08).

The models mirror ``schemas/quality-report.schema.json``: a :class:`QualityReport`
aggregates per-check :class:`CheckResult` instances, each carrying zero or more
:class:`Finding` records. The report status is derived from the worst finding
(``fail`` > ``warn`` > ``pass``).

Design notes
------------

- A single check never raises :class:`DomainError` for content issues; it
  collects findings into a :class:`CheckResult` so the caller (CLI / use case)
  can decide whether a non-zero exit code is warranted. Structural errors
  (skill_dir missing, no SKILL.md) raise
  :data:`~book2skill.domain.ErrorCode.VALIDATE_SKILL_DIR_INVALID`.
- ``Finding.location`` follows the ``<file>:<line>`` or ``<file>#<anchor>``
  convention so the report stays machine-parseable.
- :class:`~book2skill.validation.quality_report.Validator` returns
  ``published=False`` because validation is read-only. The publication
  orchestrator may copy a passing report with ``published=True`` into the
  staged tree that it commits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CheckStatus(StrEnum):
    """Status of a single check or finding."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_RUN = "not_run"


class ReportStatus(StrEnum):
    """Aggregated status of a :class:`QualityReport`.

    Mirrors the ``status`` enum in ``schemas/quality-report.schema.json``.
    """

    PASS = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    FAIL = "fail"


@dataclass(frozen=True)
class Finding:
    """One concrete issue located in the Skill tree.

    Attributes:
        severity: ``warn`` or ``fail`` (never ``pass``).
        code: Stable identifier like ``injection.hidden_char`` or
            ``copyright.long_quote``. Stable so callers can match on it.
        location: ``<file>:<line>`` or ``<file>#<anchor>``; ``""`` when the
            finding applies to the whole file.
        message: Human-readable description (zh-CN or en per locale).
    """

    severity: CheckStatus
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single check, serialisable to the report's ``checks[]``.

    Attributes:
        check_id: Stable identifier such as ``frontmatter`` |
            ``source-coverage`` | ``copyright`` | ``injection`` | ``budget`` |
            ``claim-safety``.
        status: Worst severity among findings, or :data:`CheckStatus.NOT_RUN`
            when the check was skipped.
        message: One-line summary.
        evidence: List of ``finding.code`` strings, mirroring the
            ``evidence`` array in ``schemas/quality-report.schema.json``.
    """

    check_id: str
    status: CheckStatus
    message: str
    evidence: list[str] = field(default_factory=list)

    @classmethod
    def from_findings(
        cls,
        check_id: str,
        findings: list[Finding],
        *,
        empty_message: str = "No issues found.",
    ) -> CheckResult:
        """Aggregate *findings* into a single :class:`CheckResult`.

        The worst severity wins (``fail`` > ``warn`` > ``pass``); an empty
        list yields :data:`CheckStatus.PASS`.
        """
        if not findings:
            return cls(
                check_id=check_id,
                status=CheckStatus.PASS,
                message=empty_message,
                evidence=[],
            )
        severity_rank = {CheckStatus.FAIL: 3, CheckStatus.WARN: 2, CheckStatus.PASS: 1}
        worst = max(findings, key=lambda f: severity_rank.get(f.severity, 0))
        evidence = [f.code for f in findings]
        return cls(
            check_id=check_id,
            status=worst.severity,
            message=worst.message,
            evidence=evidence,
        )


class BaseCheck:
    """Abstract base for all quality checks.

    Subclasses implement :meth:`_run` returning a list of :class:`Finding`
    objects; :meth:`run` wraps them into a :class:`CheckResult` and never
    raises for content issues (a structural failure should propagate as a
    :class:`DomainError` from the caller).
    """

    check_id: str = "base"

    def run(self, skill_dir: Path) -> CheckResult:  # noqa: D401
        """Run the check and aggregate findings into a :class:`CheckResult`."""
        findings = self._run(skill_dir)
        return CheckResult.from_findings(self.check_id, findings)

    def _run(self, skill_dir: Path) -> list[Finding]:  # pragma: no cover - abstract
        raise NotImplementedError


class QualityReport(BaseModel):
    """Aggregated quality report conforming to the JSON Schema.

    The ``checks`` array stores one object per :class:`CheckResult` with the
    keys ``check_id`` / ``status`` / ``message`` / ``evidence`` exactly as the
    schema requires. Validator-produced reports use ``published=False``;
    publication may create an updated copy after all checks pass.

    ``use_enum_values`` is intentionally left at its default (``False``) so
    ``report.status`` stays a :class:`ReportStatus` member in memory; Pydantic
    still serialises it to the schema-required string on ``model_dump_json``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    status: ReportStatus
    checks: list[dict[str, object]] = Field(default_factory=list)
    published: bool = False


__all__ = [
    "CheckStatus",
    "ReportStatus",
    "Finding",
    "CheckResult",
    "BaseCheck",
    "QualityReport",
]
