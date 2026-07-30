#!/usr/bin/env python3
"""Verify provenance state against docs/PROVENANCE.yml and local tree."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

PROVENANCE_PATH = Path("docs/PROVENANCE.yml")
LICENSES_DIR = Path("LICENSES")


class FilePorted(BaseModel):
    """Mapping between an original upstream file and its local copy."""

    original_path: str
    local_path: str
    modifications: str = ""


class ProvenanceComponent(BaseModel):
    """Single upstream component record."""

    id: str
    upstream_repo: str
    upstream_commit: str
    observed_license: str
    observed_at: str
    reuse_type: str
    status: str
    candidate_areas: list[str] = Field(default_factory=list)
    files_ported: list[FilePorted] = Field(default_factory=list)

    @field_validator("upstream_commit")
    @classmethod
    def _commit_must_be_sha(cls, value: str) -> str:
        if value == "PIN_BEFORE_ANY_CODE_REUSE":
            raise ValueError("upstream_commit is still a placeholder")
        if not re.fullmatch(r"[a-f0-9]{40}", value):
            raise ValueError("upstream_commit must be a 40-char lowercase SHA")
        return value

    @field_validator("reuse_type")
    @classmethod
    def _reuse_type_known(cls, value: str) -> str:
        allowed = {"design_reference", "selective_port", "clean_room"}
        if value not in allowed:
            raise ValueError(f"reuse_type must be one of {allowed}")
        return value


class ProvenanceFile(BaseModel):
    """Root provenance document."""

    schema_version: int
    components: list[ProvenanceComponent]


def _expected_license_path(component_id: str) -> Path:
    """Return the expected license file path for a component."""
    return LICENSES_DIR / f"MIT-{component_id}.txt"


def main() -> int:
    if not PROVENANCE_PATH.exists():
        print(f"PROVENANCE_MISSING: {PROVENANCE_PATH}")
        return 2

    raw = yaml.safe_load(PROVENANCE_PATH.read_text(encoding="utf-8"))
    try:
        provenance = ProvenanceFile(**raw)
    except ValidationError as exc:
        print(f"PROVENANCE_INVALID: {exc}")
        return 2

    errors = 0
    for comp in provenance.components:
        is_code_imported = comp.status != "no_code_imported_at_spec_stage"
        if comp.reuse_type == "selective_port" and is_code_imported:
            license_path = _expected_license_path(comp.id)
            if not license_path.exists():
                print(f"MISSING_LICENSE: {comp.id} -> {license_path}")
                errors += 1
            else:
                print(f"OK: {comp.id} selective_port tracked with {license_path.name}")
            for fp in comp.files_ported:
                local = Path(fp.local_path)
                if not local.exists():
                    print(f"MISSING_PORTED_FILE: {comp.id} -> {local}")
                    errors += 1
                else:
                    print(f"OK: {comp.id} ported {fp.original_path} -> {local}")
        else:
            print(f"OK: {comp.id} {comp.reuse_type} (status={comp.status})")

    if errors:
        print(f"FAIL: {errors} provenance issue(s)")
        return 1
    print("PASS: provenance consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
