"""Shared policy for deciding whether a validation report is publishable."""

from __future__ import annotations

from dataclasses import dataclass

from book2skill.validation.models import QualityReport, ReportStatus

REQUIRED_PUBLICATION_CHECK_IDS = frozenset(
    {"frontmatter", "source-coverage", "copyright", "injection", "budget"}
)


@dataclass(frozen=True)
class PublicationGateResult:
    """The report outcome plus fail-closed publication policy evidence."""

    report: QualityReport
    missing_check_ids: tuple[str, ...]
    not_run_check_ids: tuple[str, ...]

    @property
    def publishable(self) -> bool:
        return (
            self.report.status != ReportStatus.FAIL
            and not self.missing_check_ids
            and not self.not_run_check_ids
        )


def evaluate_publication_quality(report: QualityReport) -> PublicationGateResult:
    """Apply the mandatory-check policy to a Validator-produced report."""
    observed = {
        str(check.get("check_id", "")): str(check.get("status", ""))
        for check in report.checks
    }
    missing = tuple(sorted(REQUIRED_PUBLICATION_CHECK_IDS - set(observed)))
    not_run = tuple(
        check_id
        for check_id in sorted(REQUIRED_PUBLICATION_CHECK_IDS)
        if observed.get(check_id) == "not_run"
    )
    return PublicationGateResult(
        report=report,
        missing_check_ids=missing,
        not_run_check_ids=not_run,
    )


__all__ = [
    "PublicationGateResult",
    "REQUIRED_PUBLICATION_CHECK_IDS",
    "evaluate_publication_quality",
]
