from __future__ import annotations

import sys
import time
from pathlib import Path

from book2skill.validation.compatibility import (
    ExternalToolConfig,
    ExternalValidatorRunner,
    ValidationProfile,
    build_compatibility_report,
)
from book2skill.validation.models import QualityReport, ReportStatus


def _quality(status: ReportStatus = ReportStatus.PASS) -> QualityReport:
    return QualityReport(run_id="run-1", status=status, checks=[], published=False)


def test_draft_report_records_not_requested_tools_without_failing(
    tmp_path: Path,
) -> None:
    report = build_compatibility_report(tmp_path, _quality())

    assert report.status == ReportStatus.PASS_WITH_WARNINGS
    assert all(item.status.value == "not_run" for item in report.external_results)
    assert all(not item.blocking for item in report.external_results)


def test_release_profile_fails_when_blocking_tool_is_not_run(tmp_path: Path) -> None:
    report = build_compatibility_report(
        tmp_path,
        _quality(),
        profile=ValidationProfile.PORTABLE_RELEASE,
    )

    by_tool = {item.tool_id: item for item in report.external_results}
    assert by_tool["agent-skills-reference"].blocking is False
    assert by_tool["skill-validator"].blocking is True
    assert report.status == ReportStatus.FAIL


def test_external_runner_uses_argv_and_sanitizes_skill_path(tmp_path: Path) -> None:
    script = tmp_path / "validator.py"
    script.write_text(
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('validator 1.2.3')\n"
        "else:\n"
        "    print('checked ' + sys.argv[-1])\n",
        encoding="utf-8",
    )
    skill_dir = tmp_path / "skill with spaces"
    skill_dir.mkdir()
    config = ExternalToolConfig(
        tool_id="fake",
        expected_version="1.2.3",
        command=(sys.executable, str(script), "{skill_dir}"),
        version_command=(sys.executable, str(script), "--version"),
        blocking_in_release=True,
    )

    result = ExternalValidatorRunner().run(
        config,
        skill_dir,
        profile=ValidationProfile.PORTABLE_RELEASE,
    )

    assert result.status.value == "pass"
    assert result.observed_version == "1.2.3"
    assert str(skill_dir) not in result.message
    assert "<skill_dir>" in result.message


def test_version_mismatch_is_blocking_only_in_release(tmp_path: Path) -> None:
    script = tmp_path / "validator.py"
    script.write_text("print('validator 9.9.9')\n", encoding="utf-8")
    config = ExternalToolConfig(
        tool_id="fake",
        expected_version="1.2.3",
        command=(sys.executable, str(script), "{skill_dir}"),
        version_command=(sys.executable, str(script), "--version"),
        blocking_in_release=True,
    )
    runner = ExternalValidatorRunner()

    draft = runner.run(config, tmp_path, profile=ValidationProfile.PORTABLE_DRAFT)
    release = runner.run(config, tmp_path, profile=ValidationProfile.PORTABLE_RELEASE)

    assert draft.status.value == "warn"
    assert release.status.value == "fail"
    assert "external.version_mismatch" in release.evidence


def test_missing_external_tool_is_explicit_not_run(tmp_path: Path) -> None:
    config = ExternalToolConfig(
        tool_id="missing",
        expected_version="1.0.0",
        command=("book2skill-validator-that-does-not-exist", "{skill_dir}"),
        version_command=("book2skill-validator-that-does-not-exist", "--version"),
    )

    result = ExternalValidatorRunner().run(
        config, tmp_path, profile=ValidationProfile.PORTABLE_DRAFT
    )

    assert result.status.value == "not_run"
    assert result.evidence == ["external.tool_missing"]


def test_external_timeout_is_reported_without_shell(tmp_path: Path) -> None:
    script = tmp_path / "slow-validator.py"
    script.write_text(
        "import time\ntime.sleep(1)\nprint('validator 1.2.3')\n",
        encoding="utf-8",
    )
    config = ExternalToolConfig(
        tool_id="slow",
        expected_version="1.2.3",
        command=(sys.executable, str(script), "{skill_dir}"),
        version_command=(sys.executable, str(script), "--version"),
        blocking_in_release=True,
    )

    started = time.monotonic()
    result = ExternalValidatorRunner(timeout_seconds=0.05).run(
        config, tmp_path, profile=ValidationProfile.PORTABLE_RELEASE
    )

    assert time.monotonic() - started < 0.8
    assert result.status.value == "fail"
    assert result.evidence == ["external.timeout"]
