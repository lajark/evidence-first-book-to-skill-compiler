"""Tests for :mod:`book2skill.validation.quality_report` (Validator + writer)."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from book2skill.validation.models import (
    BaseCheck,
    CheckStatus,
    Finding,
    QualityReport,
    ReportStatus,
)
from book2skill.validation.publication_gate import evaluate_publication_quality
from book2skill.validation.quality_report import QualityReportWriter, Validator

_VALID_FRONTMATTER = (
    "---\n"
    "name: my-skill\n"
    "description: A valid skill that does something useful.\n"
    "---\n"
)


def _make_clean_skill(skill_dir: Path) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _VALID_FRONTMATTER + "# Skill\n\nNormal content.\n", encoding="utf-8"
    )
    return skill_dir


class _StubCheck(BaseCheck):
    """Stub check returning a fixed list of findings."""

    def __init__(self, check_id: str, findings: list[Finding]) -> None:
        self.check_id = check_id
        self._findings = findings

    def _run(self, skill_dir: Path) -> list[Finding]:  # noqa: ARG002
        return self._findings


class TestValidator:
    def test_aggregate_pass(self, tmp_path: Path) -> None:
        _make_clean_skill(tmp_path)
        v = Validator(
            tmp_path,
            checks=[_StubCheck("a", [])],
            now=_dt.datetime(2026, 7, 29, tzinfo=_dt.UTC),
        )
        report = v.validate()
        assert report.status == ReportStatus.PASS
        assert report.published is False
        assert len(report.checks) == 1
        assert report.checks[0]["status"] == "pass"

    def test_aggregate_warn(self, tmp_path: Path) -> None:
        _make_clean_skill(tmp_path)
        v = Validator(
            tmp_path,
            checks=[
                _StubCheck(
                    "a",
                    [Finding(CheckStatus.WARN, "a.w", "x", "warn here")],
                )
            ],
        )
        report = v.validate()
        assert report.status == ReportStatus.PASS_WITH_WARNINGS

    def test_aggregate_fail(self, tmp_path: Path) -> None:
        _make_clean_skill(tmp_path)
        v = Validator(
            tmp_path,
            checks=[
                _StubCheck(
                    "a",
                    [Finding(CheckStatus.FAIL, "a.f", "x", "fail here")],
                )
            ],
        )
        report = v.validate()
        assert report.status == ReportStatus.FAIL

    def test_missing_skill_dir_raises(self, tmp_path: Path) -> None:
        from book2skill.domain.errors import DomainError, ErrorCode

        v = Validator(tmp_path / "nope")
        with pytest.raises(DomainError) as exc:
            v.validate()
        assert exc.value.code == ErrorCode.VALIDATE_SKILL_DIR_INVALID

    def test_skill_dir_without_skill_md_raises(self, tmp_path: Path) -> None:
        from book2skill.domain.errors import DomainError, ErrorCode

        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "other.txt").write_text("x", encoding="utf-8")
        v = Validator(tmp_path)
        with pytest.raises(DomainError) as exc:
            v.validate()
        assert exc.value.code == ErrorCode.VALIDATE_SKILL_DIR_INVALID


class TestPublicationGate:
    def test_missing_required_checks_is_not_publishable(self) -> None:
        report = QualityReport(
            run_id="test",
            status=ReportStatus.PASS,
            checks=[{"check_id": "frontmatter", "status": "pass"}],
        )

        result = evaluate_publication_quality(report)

        assert result.publishable is False
        assert "injection" in result.missing_check_ids

    def test_not_run_required_check_is_not_publishable(self) -> None:
        report = QualityReport(
            run_id="test",
            status=ReportStatus.PASS,
            checks=[
                {"check_id": "frontmatter", "status": "pass"},
                {"check_id": "source-coverage", "status": "pass"},
                {"check_id": "copyright", "status": "pass"},
                {"check_id": "injection", "status": "not_run"},
                {"check_id": "budget", "status": "pass"},
            ],
        )

        result = evaluate_publication_quality(report)

        assert result.publishable is False
        assert result.not_run_check_ids == ("injection",)


class TestQualityReportWriter:
    def test_markdown_render_has_all_checks(self, tmp_path: Path) -> None:
        _make_clean_skill(tmp_path)
        report = Validator(
            tmp_path,
            checks=[
                _StubCheck("frontmatter", []),
                _StubCheck(
                    "injection",
                    [Finding(CheckStatus.WARN, "injection.x", "SKILL.md:1", "w")],
                ),
            ],
        ).validate()
        md = QualityReportWriter(skill_dir=tmp_path).to_markdown(report)
        assert "| frontmatter |" in md
        assert "| injection |" in md
        assert "## Messages" in md

    def test_json_render_matches_schema(self, tmp_path: Path) -> None:
        _make_clean_skill(tmp_path)
        report = Validator(
            tmp_path, checks=[_StubCheck("a", [])]
        ).validate()
        payload = QualityReportWriter(skill_dir=tmp_path).to_json(report)
        data = json.loads(payload)
        assert data["schema_version"] == 1
        assert data["status"] in {"pass", "pass_with_warnings", "fail"}
        assert isinstance(data["checks"], list)
        assert data["published"] is False
        for c in data["checks"]:
            assert "check_id" in c
            assert "status" in c

    def test_write_creates_both_files(self, tmp_path: Path) -> None:
        _make_clean_skill(tmp_path)
        report = Validator(
            tmp_path, checks=[_StubCheck("a", [])]
        ).validate()
        writer = QualityReportWriter(skill_dir=tmp_path)
        md_path, json_path = writer.write(report)
        assert md_path.exists()
        assert json_path.exists()
        assert md_path.read_text(encoding="utf-8").startswith("# Quality Report")
        json.loads(json_path.read_text(encoding="utf-8"))
