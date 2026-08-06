"""Regression tests for runtime scaffold integrity warnings."""

from __future__ import annotations

from pathlib import Path

from book2skill.validation.models import CheckStatus
from book2skill.validation.runtime_scaffolding_check import RuntimeScaffoldingCheck


def _make_skill(skill_dir: Path, body: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: runtime-test\ndescription: test skill\n---\n" + body,
        encoding="utf-8",
    )


def test_time_signal_without_scaffold_warns(tmp_path: Path) -> None:
    _make_skill(tmp_path, "\nUse a Pomodoro timebox and record estimate feedback.\n")

    result = RuntimeScaffoldingCheck().run(tmp_path)

    assert result.status == CheckStatus.WARN
    assert "runtime.missing_time_scaffolding" in result.evidence


def test_habit_signal_without_scaffold_warns(tmp_path: Path) -> None:
    _make_skill(tmp_path, "\nBuild a micro-habit using a cue and reward.\n")

    result = RuntimeScaffoldingCheck().run(tmp_path)

    assert result.status == CheckStatus.WARN
    assert "runtime.missing_habit_scaffolding" in result.evidence


def test_matching_scaffold_and_neutral_skill_pass(tmp_path: Path) -> None:
    _make_skill(
        tmp_path,
        "\nUse a timebox.\n## Runtime execution scaffolding\n"
        "\n## Habit execution scaffolding\n",
    )

    result = RuntimeScaffoldingCheck().run(tmp_path)

    assert result.status == CheckStatus.PASS
