"""Validate evidence levels recorded at the deterministic compile boundary."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from book2skill.application.normalized_bundle import (
    NORMALIZED_BUNDLE_FILENAME,
    NormalizedBundle,
    verify_normalized_bundle,
)
from book2skill.validation.models import BaseCheck, CheckStatus, Finding

_EXECUTABLE_KINDS = {"framework", "technique", "decision_rule", "checklist"}
_DIRECT_EVIDENCE = {"primary", "secondary"}


class EvidenceBoundaryCheck(BaseCheck):
    """Warn if executable rules rely only on inferred/user-added evidence."""

    check_id = "evidence-boundary"

    def _run(self, skill_dir: Path) -> list[Finding]:
        path = skill_dir / NORMALIZED_BUNDLE_FILENAME
        if not path.exists():
            # Backwards compatibility: pre-v1.0.1 Skills have no snapshot.
            return []
        try:
            bundle = NormalizedBundle.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError):
            return [
                Finding(
                    severity=CheckStatus.FAIL,
                    code="evidence.boundary_invalid",
                    location=NORMALIZED_BUNDLE_FILENAME,
                    message="The normalized evidence boundary is invalid.",
                )
            ]
        if not verify_normalized_bundle(bundle):
            return [
                Finding(
                    severity=CheckStatus.FAIL,
                    code="evidence.boundary_tampered",
                    location=NORMALIZED_BUNDLE_FILENAME,
                    message="The normalized evidence boundary hash does not match.",
                )
            ]
        executable = [unit for unit in bundle.units if unit.kind in _EXECUTABLE_KINDS]
        if executable and not any(
            str(unit.evidence_level) in _DIRECT_EVIDENCE for unit in executable
        ):
            return [
                Finding(
                    severity=CheckStatus.WARN,
                    code="evidence.executable_inferred_only",
                    location=NORMALIZED_BUNDLE_FILENAME,
                    message=(
                        "Executable rules rely only on inferred or user-added "
                        "evidence; add primary or secondary support."
                    ),
                )
            ]
        return []


__all__ = ["EvidenceBoundaryCheck"]
