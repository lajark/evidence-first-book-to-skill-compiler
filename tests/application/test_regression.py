from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validate
from typer.testing import CliRunner

from book2skill.application.regression import compare_skill_dirs
from book2skill.cli import app
from book2skill.resources import schema_file


def _skill(path: Path, body: str = "# Workflow\n\nDo the work.\n") -> Path:
    path.mkdir()
    (path / "SKILL.md").write_text(body, encoding="utf-8")
    references = path / "references"
    references.mkdir()
    (references / "methods.md").write_text("# Method\n", encoding="utf-8")
    return path


def test_additive_diagnostics_do_not_change_content_baseline(tmp_path: Path) -> None:
    baseline = _skill(tmp_path / "baseline")
    candidate = _skill(tmp_path / "candidate")
    (candidate / "compatibility-report.json").write_text("{}", encoding="utf-8")
    (candidate / "normalized-bundle.json").write_text("{}", encoding="utf-8")

    comparison = compare_skill_dirs(baseline, candidate)

    assert comparison.content_stable is True
    assert comparison.added == []


def test_changed_skill_content_is_reported(tmp_path: Path) -> None:
    baseline = _skill(tmp_path / "baseline")
    candidate = _skill(tmp_path / "candidate", body="# Workflow\n\nChanged.\n")

    comparison = compare_skill_dirs(baseline, candidate)

    assert comparison.content_stable is False
    assert comparison.changed == ["SKILL.md"]
    schema = json.loads(
        schema_file("regression-comparison.schema.json").read_text(encoding="utf-8")
    )
    validate(instance=comparison.model_dump(mode="json"), schema=schema)


def test_compare_cli_fails_on_content_regression(tmp_path: Path) -> None:
    baseline = _skill(tmp_path / "baseline")
    candidate = _skill(tmp_path / "candidate", body="# Workflow\n\nChanged.\n")

    result = CliRunner().invoke(
        app, ["compare-skill-artifacts", str(baseline), str(candidate), "--json"]
    )

    assert result.exit_code == 1
    assert '"content_stable": false' in result.stdout
