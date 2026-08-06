"""Check that domain-specific generated Skills retain their run-time scaffold.

The compiler selects a scaffold from the IR signals.  This read-only check
guards the final on-disk artifact as well, catching a missing fragment or an
incompatible template override without requiring the original IR at validate
time.  Findings are advisory because a custom Skill may intentionally provide
an equivalent execution contract under a different heading.
"""

from __future__ import annotations

from pathlib import Path

from book2skill.validation.models import BaseCheck, CheckStatus, Finding

_TIME_SIGNALS: tuple[str, ...] = (
    "pomodoro",
    "番茄钟",
    "番茄工作法",
    "estimate feedback",
    "estimate-versus-actual",
    "估算反馈",
    "预估",
    "实际时长",
    "偏差",
    "interruption",
    "中断",
    "short break",
    "long break",
    "短休息",
    "长休息",
    "timebox",
    "time box",
    "时间盒",
)
_HABIT_SIGNALS: tuple[str, ...] = (
    "habit",
    "micro-habit",
    "microhabit",
    "习惯",
    "微习惯",
    "cue/trigger",
    "线索",
    "奖励",
    "reward",
)

_TIME_HEADING = "## Runtime execution scaffolding"
_HABIT_HEADING = "## Habit execution scaffolding"


class RuntimeScaffoldingCheck(BaseCheck):
    """Warn when a detected method domain lacks its execution scaffold."""

    check_id = "runtime-scaffolding"

    def _run(self, skill_dir: Path) -> list[Finding]:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return []
        text = skill_md.read_text(encoding="utf-8")
        corpus = text.casefold()
        findings: list[Finding] = []
        if (
            any(signal.casefold() in corpus for signal in _TIME_SIGNALS)
            and _TIME_HEADING.casefold() not in corpus
        ):
            findings.append(
                Finding(
                    severity=CheckStatus.WARN,
                    code="runtime.missing_time_scaffolding",
                    location="SKILL.md",
                    message=(
                        "Time-box or estimate signals are present but the "
                        "runtime execution scaffold is missing; preserve "
                        "start inputs and estimate feedback fields."
                    ),
                )
            )
        if (
            any(signal.casefold() in corpus for signal in _HABIT_SIGNALS)
            and _HABIT_HEADING.casefold() not in corpus
        ):
            findings.append(
                Finding(
                    severity=CheckStatus.WARN,
                    code="runtime.missing_habit_scaffolding",
                    location="SKILL.md",
                    message=(
                        "Habit signals are present but the habit execution "
                        "scaffold is missing; preserve cue, smallest action, "
                        "record, reward and recovery fields."
                    ),
                )
            )
        return findings


__all__ = ["RuntimeScaffoldingCheck"]
