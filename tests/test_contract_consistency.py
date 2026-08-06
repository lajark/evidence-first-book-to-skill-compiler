"""Guards that public Pydantic models and JSON Schemas reject the same shape."""

from __future__ import annotations

import json

import pytest
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate
from pydantic import ValidationError as PydanticValidationError

from book2skill.application.models import AnalysisBundle
from book2skill.compiler import SkillIR
from book2skill.resources import schema_file
from book2skill.validation import (
    HostCompatibility,
    QualityReport,
    ReportStatus,
    ValidationProfile,
    VerificationLevel,
    build_compatibility_report,
)


def _schema(name: str) -> dict[str, object]:
    return json.loads(schema_file(name).read_text(encoding="utf-8"))


def test_analysis_bundle_extra_fields_are_rejected_by_both_contracts() -> None:
    payload = {
        "schema_version": 1,
        "collection_id": "collection",
        "source_ids": ["source"],
        "structure": [],
        "candidate_units": [],
        "review_queue": [],
        "unexpected": True,
    }

    with pytest.raises(PydanticValidationError):
        AnalysisBundle.model_validate(payload)
    with pytest.raises(JsonSchemaValidationError):
        validate(instance=payload, schema=_schema("analysis-bundle.schema.json"))


def test_skill_ir_usage_requires_only_declared_fields_in_both_contracts() -> None:
    payload = {
        "schema_version": 1,
        "name": "contract-demo",
        "description": "Validate the public contract shape consistently.",
        "usage": {"use_when": ["When testing."], "unexpected": True},
        "workflow": [{"step": "Validate"}],
        "knowledge_refs": [],
    }

    with pytest.raises(PydanticValidationError):
        SkillIR.model_validate(payload)
    with pytest.raises(JsonSchemaValidationError):
        validate(instance=payload, schema=_schema("skill-ir.schema.json"))


def test_compatibility_report_matches_public_schema(tmp_path) -> None:
    internal = QualityReport(run_id="run", status=ReportStatus.PASS)
    report = build_compatibility_report(
        tmp_path,
        internal,
        profile=ValidationProfile.PORTABLE_DRAFT,
        hosts=[
            HostCompatibility(
                host="codex",
                level=VerificationLevel.INSTALL_SMOKE_PASSED,
                evidence=["tests.hosts.test_codex_install"],
            )
        ],
    )

    validate(
        instance=report.model_dump(mode="json"),
        schema=_schema("compatibility-report.schema.json"),
    )
