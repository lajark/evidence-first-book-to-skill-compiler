"""Validator orchestration and quality-report rendering (TASK-016).

The :class:`Validator` runs every check in a fixed order and aggregates the
results into a :class:`~book2skill.validation.models.QualityReport` that
conforms to ``schemas/quality-report.schema.json``. The report is always
``published=False`` because validation is read-only: it never triggers
:class:`~book2skill.application.publisher.Publisher` and never swaps the
Skill directory, so a failed validation cannot overwrite a previously
published version (PRD FR-07 / FR-10).

:class:`QualityReportWriter` renders the report two ways:

- Markdown table matching ``templates/generated-skill/quality-report.md``,
  replacing the stub emitted by :class:`~book2skill.compiler.SkillWriter`.
- Machine-readable JSON conforming to the schema (used by the CLI
  ``--json`` flag).

Both outputs are written via :func:`~book2skill.storage.atomic_write` when
``--write`` is requested, so a half-written report never sits on disk.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.storage.file_storage import atomic_write
from book2skill.validation.budget_check import BudgetCheck
from book2skill.validation.copyright_check import CopyrightCheck
from book2skill.validation.frontmatter_check import FrontmatterCheck
from book2skill.validation.injection_check import InjectionCheck
from book2skill.validation.models import (
    BaseCheck,
    CheckResult,
    CheckStatus,
    QualityReport,
    ReportStatus,
)
from book2skill.validation.source_check import SourceCheck

#: Fixed order so report output is deterministic across runs.
_DEFAULT_CHECKS: list[BaseCheck] = [
    FrontmatterCheck(),
    SourceCheck(),
    CopyrightCheck(),
    InjectionCheck(),
    BudgetCheck(),
]


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


class Validator:
    """Run all quality checks against a Skill directory.

    The validator is read-only: it never modifies files under *skill_dir*.
    Writing the rendered report is the caller's responsibility (typically via
    :class:`QualityReportWriter.write` invoked from the CLI ``--write`` flag).

    Parameters:
        skill_dir: Published or draft Skill directory to validate.
        checks: Optional override of the check list (tests inject fakes).
        now: Optional clock for deterministic ``run_id`` timestamps.
    """

    def __init__(
        self,
        skill_dir: Path,
        *,
        checks: list[BaseCheck] | None = None,
        now: _dt.datetime | None = None,
    ) -> None:
        self._skill_dir = skill_dir.resolve()
        self._checks = checks if checks is not None else _DEFAULT_CHECKS
        self._now = now or _now()

    def validate(self) -> QualityReport:
        """Run all checks and return the aggregated :class:`QualityReport`.

        Raises:
            DomainError: With :data:`ErrorCode.VALIDATE_SKILL_DIR_INVALID`
                when *skill_dir* does not exist or has no ``SKILL.md``.
        """
        if not self._skill_dir.is_dir():
            raise DomainError(
                code=ErrorCode.VALIDATE_SKILL_DIR_INVALID,
                input_id=str(self._skill_dir),
                message=f"Skill directory not found: {self._skill_dir}",
                recovery="Point at a directory produced by `book2skill build`.",
            )
        if not (self._skill_dir / "SKILL.md").exists():
            raise DomainError(
                code=ErrorCode.VALIDATE_SKILL_DIR_INVALID,
                input_id=str(self._skill_dir),
                message=(
                    f"Skill directory has no SKILL.md: {self._skill_dir}"
                ),
                recovery="Run `book2skill build` to produce a Skill directory.",
            )

        results: list[CheckResult] = []
        for check in self._checks:
            results.append(check.run(self._skill_dir))

        status = self._aggregate(results)
        return QualityReport(
            run_id=self._run_id(),
            status=status,
            checks=[self._result_to_dict(r) for r in results],
            published=False,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_id(self) -> str:
        """A deterministic run id combining skill name and timestamp."""
        name = self._skill_dir.name
        ts = self._now.strftime("%Y%m%dT%H%M%S")
        return f"{name}-{ts}"

    @staticmethod
    def _aggregate(results: list[CheckResult]) -> ReportStatus:
        """Map per-check statuses to a single :class:`ReportStatus`."""
        has_warn = False
        for r in results:
            if r.status == CheckStatus.FAIL:
                return ReportStatus.FAIL
            if r.status == CheckStatus.WARN:
                has_warn = True
        return (
            ReportStatus.PASS_WITH_WARNINGS if has_warn else ReportStatus.PASS
        )

    @staticmethod
    def _result_to_dict(result: CheckResult) -> dict[str, object]:
        """Serialise a :class:`CheckResult` to the schema's ``checks[]`` shape."""
        return {
            "check_id": result.check_id,
            "status": result.status.value,
            "message": result.message,
            "evidence": list(result.evidence),
        }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityReportWriter:
    """Render a :class:`QualityReport` as Markdown and JSON.

    Stateless so a single instance can render many reports; the *skill_dir*
    is captured at construction only to compute the on-disk output paths.
    """

    skill_dir: Path

    def to_markdown(self, report: QualityReport) -> str:
        """Render the Markdown table matching the template structure."""
        lines = [
            "# Quality Report",
            "",
            f"- Run: `{report.run_id}`",
            f"- Status: `{report.status.value}`",
            f"- Published: `{str(report.published).lower()}`",
            "",
            "## Checks",
            "",
            "| Check | Status | Evidence |",
            "|---|---|---|",
        ]
        for check in report.checks:
            check_id = str(check.get("check_id", ""))
            status = str(check.get("status", ""))
            evidence_raw = check.get("evidence", [])
            evidence: list[str] = (
                [str(e) for e in evidence_raw]
                if isinstance(evidence_raw, list)
                else []
            )
            evidence_str = ", ".join(evidence) if evidence else "—"
            lines.append(f"| {check_id} | {status} | {evidence_str} |")
        lines.append("")
        # Append a human-readable message section so reviewers can act on it.
        lines.append("## Messages")
        lines.append("")
        for check in report.checks:
            check_id = str(check.get("check_id", ""))
            message = str(check.get("message", ""))
            lines.append(f"- **{check_id}**: {message}")
        lines.append("")
        return "\n".join(lines)

    def to_json(self, report: QualityReport) -> str:
        """Render the report as JSON conforming to the schema."""
        return report.model_dump_json(indent=2)

    def write(self, report: QualityReport) -> tuple[Path, Path]:
        """Atomically write ``quality-report.md`` and ``quality-report.json``.

        Returns the two written paths. Existing files are replaced atomically;
        other Skill files (``SKILL.md`` / ``references/`` / ``provenance.yml``)
        are never touched.
        """
        md_path = self.skill_dir / "quality-report.md"
        json_path = self.skill_dir / "quality-report.json"
        atomic_write(md_path, self.to_markdown(report))
        atomic_write(json_path, self.to_json(report))
        return md_path, json_path


__all__ = ["Validator", "QualityReportWriter"]
