"""Machine-readable, evidence-bound quality metrics for acceptance runs.

The helpers deliberately measure only properties that can be verified from an
Analyze bundle or a generated Skill tree.  ``unsupported_candidate_count`` is
an auditable proxy for unsupported output, not a semantic hallucination score;
semantic truthfulness still requires human or task-specific evaluation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_CITATION_RE = re.compile(
    r"^\s*-\s+(?P<source_id>[^\s/]+)\s*/\s*(?P<block_id>[^\s]+)",
    re.MULTILINE,
)


def analyze_bundle_metrics(payload: dict[str, Any]) -> dict[str, int | float]:
    """Return provenance and duplicate-ID metrics for an Analyze JSON payload."""
    source_ids = {
        source_id
        for source_id in payload.get("source_ids", [])
        if isinstance(source_id, str) and source_id
    }
    candidates = payload.get("candidate_units", [])
    if not isinstance(candidates, list):
        candidates = []

    candidate_ids = [
        candidate.get("unit_id")
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("unit_id"), str)
    ]
    supported = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        refs = candidate.get("source_refs", [])
        if not isinstance(refs, list):
            continue
        if any(
            isinstance(ref, dict)
            and isinstance(ref.get("source_id"), str)
            and ref["source_id"] in source_ids
            and isinstance(ref.get("block_id"), str)
            and bool(ref["block_id"])
            for ref in refs
        ):
            supported += 1

    total = len(candidates)
    return {
        "source_count": len(source_ids),
        "candidate_count": total,
        "supported_candidate_count": supported,
        "unsupported_candidate_count": total - supported,
        "source_reference_coverage": supported / total if total else 1.0,
        "duplicate_candidate_id_count": len(candidate_ids) - len(set(candidate_ids)),
    }


def skill_tree_metrics(skill_dir: Path) -> dict[str, int | float]:
    """Return citation, quotation and quality-gate metrics for a Skill tree."""
    provenance = _load_mapping(skill_dir / "provenance.yml")
    sources = provenance.get("sources", []) if isinstance(provenance, dict) else []
    declared = {
        item.get("source_id")
        for item in sources
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    citations: set[tuple[str, str]] = set()
    for markdown in sorted(skill_dir.glob("SKILL.md")) + sorted(
        (skill_dir / "references").glob("*.md")
    ):
        if markdown.name == "quality-report.md" or not markdown.exists():
            continue
        for match in _CITATION_RE.finditer(markdown.read_text(encoding="utf-8")):
            citations.add((match.group("source_id"), match.group("block_id")))

    cited_sources = {source_id for source_id, _ in citations}
    report = _load_mapping(skill_dir / "quality-report.json")
    checks = report.get("checks", []) if isinstance(report, dict) else []
    evidence = {
        code
        for check in checks
        if isinstance(check, dict)
        for code in check.get("evidence", [])
        if isinstance(code, str)
    }
    integrity = _load_mapping(skill_dir / "content-integrity.json")
    return {
        "declared_source_count": len(declared),
        "cited_source_count": len(cited_sources),
        "citation_count": len(citations),
        "citation_source_hit_rate": (
            len(declared & cited_sources) / len(declared) if declared else 1.0
        ),
        "orphan_citation_source_count": len(cited_sources - declared),
        "long_quote_finding_count": sum(
            code.startswith("copyright.") for code in evidence
        ),
        "unsupported_output_proxy_count": sum(
            code
            in {
                "source.no_provenance",
                "source.empty_provenance",
                "source.undeclared",
            }
            for code in evidence
        ),
        "quality_fail_check_count": sum(
            isinstance(check, dict) and check.get("status") == "fail"
            for check in checks
        ),
        "content_integrity_blocked": int(integrity.get("blocked", False)),
        "content_integrity_missing_unit_count": len(
            integrity.get("missing_unit_ids", [])
            if isinstance(integrity.get("missing_unit_ids", []), list)
            else []
        ),
        "content_integrity_mismatch_count": len(
            integrity.get("content_mismatch_unit_ids", [])
            if isinstance(integrity.get("content_mismatch_unit_ids", []), list)
            else []
        ),
    }


def acceptance_failures(metrics: dict[str, int | float]) -> list[str]:
    """Return invariant failures suitable for an automated acceptance verdict."""
    failures: list[str] = []
    if metrics.get("source_reference_coverage", 1.0) != 1.0:
        failures.append("candidate source-reference coverage is below 100%")
    if metrics.get("unsupported_candidate_count", 0) != 0:
        failures.append("Analyze output contains candidate(s) without a trusted source")
    if metrics.get("duplicate_candidate_id_count", 0) != 0:
        failures.append("Analyze output contains duplicate candidate IDs")
    if metrics.get("citation_source_hit_rate", 1.0) != 1.0:
        failures.append("generated Skill does not cite every declared source")
    if metrics.get("orphan_citation_source_count", 0) != 0:
        failures.append("generated Skill cites undeclared source(s)")
    if metrics.get("unsupported_output_proxy_count", 0) != 0:
        failures.append("quality gate found output without usable provenance")
    if metrics.get("quality_fail_check_count", 0) != 0:
        failures.append("generated Skill has failing quality gate(s)")
    if metrics.get("content_integrity_blocked", 0) != 0:
        failures.append("generated Skill content-integrity gate is blocked")
    return failures


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.suffix == ".json":
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


__all__ = ["acceptance_failures", "analyze_bundle_metrics", "skill_tree_metrics"]
