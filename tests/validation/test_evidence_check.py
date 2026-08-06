from __future__ import annotations

from pathlib import Path

from book2skill.application.normalized_bundle import (
    normalize_units,
    write_normalized_bundle,
)
from book2skill.domain import (
    EvidenceLevel,
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    UnitKind,
)
from book2skill.validation import CheckStatus, EvidenceBoundaryCheck


def _unit(level: EvidenceLevel) -> KnowledgeUnit:
    return KnowledgeUnit(
        unit_id="u-1",
        kind=UnitKind.TECHNIQUE,
        content="Execute the documented method.",
        source_refs=[KnowledgeRef(source_id="src-1", block_id="b-1")],
        review_status=KnowledgeStatus.APPROVED,
        evidence_level=level,
        evidence_note=(
            "Reviewed inference." if level == EvidenceLevel.INFERRED else None
        ),
    )


def _write(tmp_path: Path, level: EvidenceLevel) -> None:
    result = normalize_units(
        collection_id="collection-1",
        source_ids=["src-1"],
        units=[_unit(level)],
    )
    write_normalized_bundle(tmp_path, result.bundle)


def test_direct_evidence_passes(tmp_path: Path) -> None:
    _write(tmp_path, EvidenceLevel.PRIMARY)

    result = EvidenceBoundaryCheck().run(tmp_path)

    assert result.status == CheckStatus.PASS


def test_inferred_only_executable_rules_warn(tmp_path: Path) -> None:
    _write(tmp_path, EvidenceLevel.INFERRED)

    result = EvidenceBoundaryCheck().run(tmp_path)

    assert result.status == CheckStatus.WARN
    assert result.evidence == ["evidence.executable_inferred_only"]


def test_tampered_boundary_fails(tmp_path: Path) -> None:
    _write(tmp_path, EvidenceLevel.PRIMARY)
    path = tmp_path / "normalized-bundle.json"
    path.write_text(path.read_text(encoding="utf-8").replace("u-1", "u-2"))

    result = EvidenceBoundaryCheck().run(tmp_path)

    assert result.status == CheckStatus.FAIL
    assert result.evidence == ["evidence.boundary_tampered"]
