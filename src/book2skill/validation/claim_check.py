"""Detect unqualified guarantee and causal claims in generated Skills.

This is a conservative, offline warning check.  It does not try to decide
whether a claim is scientifically true; it identifies wording that needs an
explicit source, scope, uncertainty or attribution before publication.  The
check deliberately reports warnings rather than failures because a cited
source may legitimately contain such wording and the validator has no
semantic model for resolving that context.
"""

from __future__ import annotations

import re
from pathlib import Path

from book2skill.validation.models import BaseCheck, CheckStatus, Finding

_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "claims.unqualified_guarantee",
        re.compile(
            r"保证(?:了|会|能|可以|有效|成功|持续|实现)?|"
            r"必然(?:会|能|导致)?|"
            r"一定(?:会|能|可以|有效|成功)|"
            r"(?:永远|绝对)(?:不会|有效|正确)?"
        ),
    ),
    (
        "claims.unqualified_causality",
        re.compile(
            r"证明(?:了|其|这|该)?|"
            r"说明(?:了|其|这|该)?.{0,12}(?:负责|导致|决定)"
        ),
    ),
    (
        "claims.unqualified_guarantee",
        re.compile(
            r"\b(?:guarantee(?:s|d)?|ensure(?:s|d)?|always|never\s+fails)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "claims.unqualified_causality",
        re.compile(r"\b(?:prove(?:s|d)?|demonstrate(?:s|d)?)\b", re.IGNORECASE),
    ),
)

_NEGATION_PREFIXES = (
    "不",
    "未",
    "无",
    "并不",
    "并未",
    "does not ",
    "do not ",
    "doesn't ",
    "don't ",
    "not ",
)


def _is_negated(line: str, start: int) -> bool:
    """Avoid flagging an explicit warning such as ``不保证`` or ``not prove``."""
    prefix = line[max(0, start - 12) : start].casefold()
    return any(prefix.endswith(item.casefold()) for item in _NEGATION_PREFIXES)


def _is_source_quote(line: str) -> bool:
    """Skip the quoted excerpt line emitted in ``references`` provenance."""
    return bool(
        re.match(
            r"^\s*-\s+[^\s/]+\s*/\s*[^\s]+\s+—\s+\"",
            line,
        )
    )


class ClaimSafetyCheck(BaseCheck):
    """Warn when generated prose uses unqualified high-risk claim wording."""

    check_id = "claim-safety"

    def _run(self, skill_dir: Path) -> list[Finding]:
        findings: list[Finding] = []
        targets = [skill_dir / "SKILL.md"]
        references_dir = skill_dir / "references"
        if references_dir.is_dir():
            targets.extend(sorted(references_dir.glob("*.md")))

        for path in targets:
            if not path.exists() or path.name == "quality-report.md":
                continue
            rel_path = path.relative_to(skill_dir).as_posix()
            in_fence = False
            for line_no, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if line.lstrip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence or _is_source_quote(line):
                    continue
                for code, pattern in _CLAIM_PATTERNS:
                    for match in pattern.finditer(line):
                        if _is_negated(line, match.start()):
                            continue
                        findings.append(
                            Finding(
                                severity=CheckStatus.WARN,
                                code=code,
                                location=f"{rel_path}:{line_no}",
                                message=(
                                    "Unqualified guarantee or causal wording "
                                    f"'{match.group(0)}' detected; attribute it "
                                    "to evidence or qualify scope and uncertainty."
                                ),
                            )
                        )
        return findings


__all__ = ["ClaimSafetyCheck"]
