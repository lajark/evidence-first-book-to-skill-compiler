"""Regression tests for the advisory claim-safety quality check."""

from __future__ import annotations

from pathlib import Path

from book2skill.validation.claim_check import ClaimSafetyCheck
from book2skill.validation.models import CheckStatus


def _make_skill(skill_dir: Path, body: str, *, reference: str | None = None) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: claim-test\ndescription: test skill\n---\n" + body,
        encoding="utf-8",
    )
    if reference is not None:
        refs = skill_dir / "references"
        refs.mkdir()
        (refs / "evidence.md").write_text(reference, encoding="utf-8")


def test_unqualified_chinese_claims_warn(tmp_path: Path) -> None:
    _make_skill(tmp_path, "\n该方法保证持续行动，并证明其负责结果。\n")

    result = ClaimSafetyCheck().run(tmp_path)

    assert result.status == CheckStatus.WARN
    assert "claims.unqualified_guarantee" in result.evidence
    assert "claims.unqualified_causality" in result.evidence


def test_qualified_or_negated_claims_are_not_flagged(tmp_path: Path) -> None:
    _make_skill(
        tmp_path,
        "\n来源未证明该方法适用于所有人；本流程不保证结果。\n",
    )

    result = ClaimSafetyCheck().run(tmp_path)

    assert result.status == CheckStatus.PASS


def test_source_quote_and_code_are_not_repeated_as_claim_findings(
    tmp_path: Path,
) -> None:
    _make_skill(
        tmp_path,
        '\n```text\nThis method guarantees success.\n```\n',
        reference='- src-1 / b-1 — "该方法保证成功"\n',
    )

    result = ClaimSafetyCheck().run(tmp_path)

    assert result.status == CheckStatus.PASS
