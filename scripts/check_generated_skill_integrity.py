"""Verify a final Skill directory as a generated-artifact carrier.

This is intentionally separate from the Build/Publish in-memory gate.  It
reopens the normalized bundle, recompares every rendered unit and verifies the
compilation manifest plus the persisted completeness report.  Pass the
authoritative SourceManifest JSON export to verify source hashes as well;
without it the command is explicitly an internal-only consistency check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from book2skill.application.artifacts import (  # noqa: E402
    load_compilation_artifact,
    verify_compilation_artifact,
)
from book2skill.application.content_integrity import (  # noqa: E402
    CONTENT_INTEGRITY_FILENAME,
    ContentIntegrityReport,
    check_generated_skill_content,
)
from book2skill.application.normalized_bundle import (  # noqa: E402
    load_normalized_bundle,
)
from book2skill.domain import SourceManifest  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed completeness and carrier check for a Skill tree."
    )
    parser.add_argument("skill_dir", type=Path)
    parser.add_argument(
        "--source-manifests",
        type=Path,
        help=(
            "JSON list (or {'sources': [...]}) of authoritative "
            "SourceManifest records."
        ),
    )
    return parser


def _load_manifests(
    path: Path | None, skill_dir: Path
) -> tuple[list[SourceManifest], bool]:
    if path is None:
        # Internal-only mode is useful for a copied carrier, but cannot prove
        # that provenance hashes still match the original Raw records.
        provenance = skill_dir / "provenance.yml"
        data = yaml.safe_load(provenance.read_text(encoding="utf-8"))
        provenance_records = (
            data.get("sources", []) if isinstance(data, dict) else []
        )
        manifests: list[SourceManifest] = []
        for record in provenance_records:
            if not isinstance(record, dict):
                continue
            manifests.append(
                SourceManifest.model_validate(
                    {
                        "source_id": record["source_id"],
                        "version": 1,
                        "original_name": record.get("title"),
                        "content_sha256": record["content_sha256"],
                        "format": record.get("format", "txt"),
                        "rights_confirmed": True,
                        "ingested_at": record.get("ingested_at"),
                    }
                )
            )
        return manifests, False

    raw = json.loads(path.read_text(encoding="utf-8"))
    records: Any = raw.get("sources", []) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise ValueError(
            "source manifest JSON must be a list or an object with sources"
        )
    return [SourceManifest.model_validate(item) for item in records], True


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    skill_dir = args.skill_dir.resolve()
    try:
        normalized = load_normalized_bundle(skill_dir)
        manifests, authoritative = _load_manifests(args.source_manifests, skill_dir)
        artifact = load_compilation_artifact(skill_dir)
        artifact_ok = artifact is not None and verify_compilation_artifact(
            skill_dir, artifact, units=None
        )
        report = check_generated_skill_content(
            skill_dir,
            units=cast(Any, normalized.units),
            source_manifests=manifests,
        )
        persisted = ContentIntegrityReport.model_validate_json(
            (skill_dir / CONTENT_INTEGRITY_FILENAME).read_text(encoding="utf-8")
        )
        report_match = persisted == report
        result = {
            "skill_dir": str(skill_dir),
            "artifact_ok": artifact_ok,
            "content_report_match": report_match,
            "authoritative_source_check": authoritative,
            "blocked": (
                not artifact_ok
                or not report_match
                or report.blocked
                or not authoritative
            ),
            "content_integrity": report.model_dump(mode="json"),
        }
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        result = {"blocked": True, "error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("blocked"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
