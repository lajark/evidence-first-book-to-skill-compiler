"""Multi-book Asset Pack governance analysis for B2S-M13-04.

This layer classifies candidate assets before the Pack publisher is called. It
does not vote between authors or silently route conflicting content. Exact
duplicates can merge with complete provenance; disjoint scopes are retained as
explicit coexistence-review items; overlapping scopes with different hashes
remain blocked conflicts.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from book2skill.domain.errors import ErrorCode
from book2skill.runtime.closure import ProvenanceRef
from book2skill.runtime.pack_update import (
    AssetCandidate,
    AssetPackRelease,
    PackCandidate,
    PackUpdateError,
)


class ScopeRelation(StrEnum):
    """Applicability relation between an existing and candidate asset."""

    NEW = "new"
    DUPLICATE = "duplicate"
    DISJOINT = "disjoint"
    OVERLAPPING = "overlapping"


class MergeDisposition(StrEnum):
    """Governance action permitted for a comparison."""

    ADD = "add"
    MERGE = "merge"
    COEXIST_REVIEW = "coexist_review"
    CONFLICT_REVIEW = "conflict_review"


class AssetComparison(BaseModel):
    """One candidate-versus-known asset governance decision."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    canonical_key: str = Field(..., min_length=1)
    relation: ScopeRelation
    disposition: MergeDisposition
    existing_hash: str | None = None
    candidate_hash: str = Field(..., min_length=64, max_length=64)
    existing_scope: list[str] = Field(default_factory=list)
    candidate_scope: list[str] = Field(..., min_length=1)
    overlapping_scope: list[str] = Field(default_factory=list)
    existing_provenance: list[ProvenanceRef] = Field(default_factory=list)
    candidate_provenance: list[ProvenanceRef] = Field(..., min_length=1)


class MultiBookMergePlan(BaseModel):
    """Auditable multi-book classification before production publication."""

    model_config = ConfigDict(extra="forbid")

    task_contract_id: str = Field(..., min_length=1)
    pack_id: str = Field(..., min_length=1)
    base_pack_version: str = Field(..., min_length=1)
    comparisons: list[AssetComparison] = Field(..., min_length=1)
    provenance_by_key: dict[str, list[ProvenanceRef]] = Field(default_factory=dict)

    @property
    def duplicates(self) -> list[AssetComparison]:
        return [
            item
            for item in self.comparisons
            if item.disposition == MergeDisposition.MERGE
        ]

    @property
    def scoped_variants(self) -> list[AssetComparison]:
        return [
            item
            for item in self.comparisons
            if item.disposition == MergeDisposition.COEXIST_REVIEW
        ]

    @property
    def conflicts(self) -> list[AssetComparison]:
        return [
            item
            for item in self.comparisons
            if item.disposition == MergeDisposition.CONFLICT_REVIEW
        ]

    @property
    def publishable(self) -> bool:
        """Whether no human scope/conflict decision remains outstanding."""

        return not self.conflicts and not self.scoped_variants

    def assert_publishable(self) -> None:
        """Fail closed instead of silently selecting an author's version."""

        if self.conflicts:
            raise PackUpdateError(
                ErrorCode.PACK_UPDATE_CONFLICT,
                self.task_contract_id,
                "Overlapping-scope author conflict requires human adjudication",
                details={
                    "canonical_keys": sorted(
                        {item.canonical_key for item in self.conflicts}
                    )
                },
            )
        if self.scoped_variants:
            raise PackUpdateError(
                ErrorCode.PACK_UPDATE_SCOPE_REVIEW_REQUIRED,
                self.task_contract_id,
                "Disjoint-scope variants require explicit routing before publish",
                details={
                    "canonical_keys": sorted(
                        {item.canonical_key for item in self.scoped_variants}
                    )
                },
            )


def _scope_relation(
    existing_scope: Iterable[str], candidate_scope: Iterable[str]
) -> tuple[ScopeRelation, list[str]]:
    left = {item.strip().lower() for item in existing_scope}
    right = {item.strip().lower() for item in candidate_scope}
    overlap = sorted(left & right)
    return (
        (ScopeRelation.OVERLAPPING, overlap)
        if overlap
        else (ScopeRelation.DISJOINT, [])
    )


def _unique_provenance(
    *groups: Iterable[ProvenanceRef],
) -> list[ProvenanceRef]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[ProvenanceRef] = []
    for group in groups:
        for item in group:
            key = (item.source_id, item.locator, item.source_sha256)
            if key not in seen:
                seen.add(key)
                result.append(item)
    return result


class MultiBookGovernance:
    """Classify a batch of assets from multiple books against one Pack."""

    def analyze(
        self,
        base: AssetPackRelease,
        candidates: Iterable[AssetCandidate] | PackCandidate,
    ) -> MultiBookMergePlan:
        if isinstance(candidates, PackCandidate):
            if (
                candidates.base_pack_id != base.pack_id
                or candidates.base_pack_version != base.version
                or candidates.task_contract_id != base.task_contract_id
                or candidates.task_contract_version != base.task_contract_version
                or candidates.skill_kernel_id != base.skill_kernel_id
                or candidates.skill_kernel_version != base.skill_kernel_version
                or candidates.skill_kernel_sha256 != base.skill_kernel_sha256
            ):
                raise PackUpdateError(
                    ErrorCode.PACK_UPDATE_CONTRACT_MISMATCH,
                    base.task_contract_id,
                    "Multi-book candidate is not compatible with the base Pack",
                )
            candidate_list = list(candidates.assets)
        else:
            candidate_list = list(candidates)
        if not candidate_list:
            raise PackUpdateError(
                ErrorCode.PACK_UPDATE_INVALID,
                base.task_contract_id,
                "Multi-book governance requires at least one candidate asset",
            )

        known: dict[str, list[tuple[str, list[str], list[ProvenanceRef]]]] = {}
        for base_asset in base.assets:
            known.setdefault(base_asset.canonical_key, []).append(
                (
                    base_asset.content_sha256,
                    base_asset.scope,
                    base_asset.provenance,
                )
            )
        comparisons: list[AssetComparison] = []
        provenance_by_key: dict[str, list[ProvenanceRef]] = {
            base_asset.canonical_key: _unique_provenance(base_asset.provenance)
            for base_asset in base.assets
        }
        for candidate_asset in candidate_list:
            provenance_by_key[candidate_asset.canonical_key] = _unique_provenance(
                provenance_by_key.get(candidate_asset.canonical_key, []),
                candidate_asset.provenance,
            )
            observations = known.get(candidate_asset.canonical_key, [])
            if not observations:
                comparisons.append(
                    AssetComparison(
                        canonical_key=candidate_asset.canonical_key,
                        relation=ScopeRelation.NEW,
                        disposition=MergeDisposition.ADD,
                        candidate_hash=candidate_asset.content_sha256,
                        candidate_scope=candidate_asset.scope,
                        candidate_provenance=candidate_asset.provenance,
                    )
                )
                known.setdefault(candidate_asset.canonical_key, []).append(
                    (
                        candidate_asset.content_sha256,
                        candidate_asset.scope,
                        candidate_asset.provenance,
                    )
                )
                continue

            matching = next(
                (
                    observation
                    for observation in observations
                    if observation[0] == candidate_asset.content_sha256
                ),
                None,
            )
            if matching is not None:
                comparisons.append(
                    AssetComparison(
                        canonical_key=candidate_asset.canonical_key,
                        relation=ScopeRelation.DUPLICATE,
                        disposition=MergeDisposition.MERGE,
                        existing_hash=matching[0],
                        candidate_hash=candidate_asset.content_sha256,
                        existing_scope=matching[1],
                        candidate_scope=candidate_asset.scope,
                        existing_provenance=matching[2],
                        candidate_provenance=candidate_asset.provenance,
                    )
                )
                known[candidate_asset.canonical_key] = [
                    (
                        matching[0],
                        list(
                            dict.fromkeys((*matching[1], *candidate_asset.scope))
                        ),
                        _unique_provenance(matching[2], candidate_asset.provenance),
                    )
                ]
                continue

            existing = observations[0]
            relation, overlap = _scope_relation(
                existing[1], candidate_asset.scope
            )
            disposition = (
                MergeDisposition.CONFLICT_REVIEW
                if relation == ScopeRelation.OVERLAPPING
                else MergeDisposition.COEXIST_REVIEW
            )
            comparisons.append(
                AssetComparison(
                    canonical_key=candidate_asset.canonical_key,
                    relation=relation,
                    disposition=disposition,
                    existing_hash=existing[0],
                    candidate_hash=candidate_asset.content_sha256,
                    existing_scope=existing[1],
                    candidate_scope=candidate_asset.scope,
                    overlapping_scope=overlap,
                    existing_provenance=existing[2],
                    candidate_provenance=candidate_asset.provenance,
                )
            )
            # Keep the candidate observation, so another book is compared
            # against both authors rather than silently replacing either one.
            known[candidate_asset.canonical_key].append(
                (
                    candidate_asset.content_sha256,
                    candidate_asset.scope,
                    candidate_asset.provenance,
                )
            )

        return MultiBookMergePlan(
            task_contract_id=base.task_contract_id,
            pack_id=base.pack_id,
            base_pack_version=base.version,
            comparisons=comparisons,
            provenance_by_key=provenance_by_key,
        )
