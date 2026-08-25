"""Production bridge for Pack-only incremental publication.

The normal Update path still rebuilds a whole Skill tree.  This explicit API
is the task-centered path for callers that already converted newly analyzed
book material into source-provenanced :class:`AssetCandidate` records.  It
persists the candidate/review evidence and changes only the Pack active
pointer; Kernel and the existing Skill directory are never written here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from book2skill.application.models import AnalysisBundle
from book2skill.domain import SourceManifest
from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.runtime.closure import ProvenanceRef
from book2skill.runtime.multi_book import MultiBookGovernance
from book2skill.runtime.pack_closure import PackClosureBinder, PackClosureBinding
from book2skill.runtime.pack_store import PersistentPackStore
from book2skill.runtime.pack_update import (
    AssetCandidate,
    AssetPackRelease,
    PackCandidate,
    PackPublisher,
    PackRollback,
    ReviewDecision,
)


@dataclass(frozen=True)
class PackIncrementalResult:
    """Auditable result of one Pack-only candidate/review/publication run."""

    candidate: PackCandidate
    release: AssetPackRelease | None
    closure_binding: PackClosureBinding | None = None


class PackIncrementalPublisher:
    """Persist and publish an approved multi-book Pack candidate."""

    def __init__(self, store: PersistentPackStore) -> None:
        self._store = store
        self._publisher = PackPublisher()
        self._governance = MultiBookGovernance()

    def execute(
        self,
        base: AssetPackRelease,
        *,
        candidate_id: str,
        assets: Sequence[AssetCandidate],
        reviewer: str,
        version: str,
        confirm: bool,
        regression: Callable[[AssetPackRelease], bool],
        closure_root: Path | None = None,
    ) -> PackIncrementalResult:
        """Run candidate → review → approval and optionally activate a release.

        ``confirm=False`` persists the approved candidate as review evidence but
        does not write a release or change the active pointer.  ``confirm=True``
        requires governance to classify every multi-book asset as publishable,
        then uses the persistent optimistic-base gate before activation. When
        ``closure_root`` is supplied, the active Pack and production Closure
        are rebound together with compensating rollback on a binding failure.
        """

        candidate = self._publisher.create_candidate(
            base, candidate_id=candidate_id, assets=list(assets)
        )
        self._store.save_candidate(candidate)
        decisions = [
            ReviewDecision(
                asset_id=asset.asset_id,
                decision="approve",
                reviewer=reviewer,
                reason="Pack-only incremental review",
            )
            for asset in candidate.assets
        ]
        reviewed = self._publisher.review(candidate, decisions)
        approved = self._publisher.approve(reviewed)
        self._store.save_candidate(approved)
        if not confirm:
            return PackIncrementalResult(candidate=approved, release=None)

        plan = self._governance.analyze(base, approved)
        plan.assert_publishable()
        if closure_root is not None:
            current = self._store.active(base.pack_id)
            if current is not None and current.pack_hash != base.pack_hash:
                # Let the persistent store's optimistic-base error remain the
                # single source of truth; do not mutate a Closure for a stale
                # candidate.
                current = None
            if current is not None:
                PackClosureBinder.bind(closure_root, base)
        release = self._store.publish_incremental(
            base,
            approved,
            version=version,
            regression=regression,
        )
        if closure_root is None:
            return PackIncrementalResult(candidate=approved, release=release)
        try:
            binding = PackClosureBinder.bind(closure_root, release)
        except Exception:  # noqa: BLE001 - compensate Pack/Closure swap
            try:
                self._store.rollback(base.pack_id, base.version)
                PackClosureBinder.bind(closure_root, base)
            except Exception as rollback_exc:  # noqa: BLE001 - hard boundary
                raise DomainError(
                    code=ErrorCode.PUBLISH_ROLLBACK_FAILED,
                    input_id=base.pack_id,
                    message="Pack publication failed and Pack/Closure rollback failed",
                    recovery=(
                        "Restore the prior Pack release and Closure snapshot manually."
                    ),
                ) from rollback_exc
            raise
        return PackIncrementalResult(
            candidate=approved,
            release=release,
            closure_binding=binding,
        )

    def rollback(
        self,
        pack_id: str,
        target_version: str,
        *,
        closure_root: Path | None = None,
    ) -> PackRollback:
        """Rollback Pack active and rebind its production Closure when given."""

        current = self._store.active(pack_id)
        rollback = self._store.rollback(pack_id, target_version)
        if closure_root is None:
            return rollback
        try:
            PackClosureBinder.bind(closure_root, rollback.selected_pack)
        except Exception:  # noqa: BLE001 - compensate Pack/Closure swap
            try:
                if current is not None:
                    self._store.rollback(pack_id, current.version)
                    PackClosureBinder.bind(closure_root, current)
            except Exception as rollback_exc:  # noqa: BLE001 - hard boundary
                raise DomainError(
                    code=ErrorCode.PUBLISH_ROLLBACK_FAILED,
                    input_id=pack_id,
                    message="Pack rollback failed and Pack/Closure restoration failed",
                    recovery=(
                        "Restore the prior Pack release and Closure snapshot manually."
                    ),
                ) from rollback_exc
            raise
        return rollback


def asset_candidates_from_analysis(
    bundle: AnalysisBundle,
    source_manifests: Iterable[SourceManifest],
) -> list[AssetCandidate]:
    """Project analyzed units into source-provenanced Pack assets.

    Identity is derived from normalized unit content while every source block
    is retained with the authoritative source hash. Rejected or superseded
    units never enter a candidate.
    """

    manifest_list = tuple(source_manifests)
    manifests = {manifest.source_id: manifest for manifest in manifest_list}
    if len(manifests) != len(manifest_list):
        raise DomainError(
            code=ErrorCode.PACK_UPDATE_INVALID,
            input_id=bundle.collection_id,
            message="Analysis projection received duplicate source manifests",
            recovery="Provide one authoritative SourceManifest per source_id.",
        )
    candidates: list[AssetCandidate] = []
    for unit in bundle.candidate_units:
        if unit.review_status in {"rejected", "superseded"}:
            continue
        provenance: list[ProvenanceRef] = []
        for source_ref in unit.source_refs:
            source_id = str(source_ref.get("source_id", "")).strip()
            block_id = str(source_ref.get("block_id", "")).strip()
            manifest = manifests.get(source_id)
            if not source_id or not block_id or manifest is None:
                raise DomainError(
                    code=ErrorCode.PACK_UPDATE_INVALID,
                    input_id=unit.unit_id,
                    message=(
                        "Analysis unit source reference is not covered by a manifest"
                    ),
                    recovery="Re-run analysis with immutable Raw manifests available.",
                    details={"source_id": source_id, "block_id": block_id},
                )
            provenance.append(
                ProvenanceRef(
                    source_id=source_id,
                    locator=f"block:{block_id}",
                    source_sha256=manifest.content_sha256,
                )
            )
        if not provenance:
            raise DomainError(
                code=ErrorCode.PACK_UPDATE_INVALID,
                input_id=unit.unit_id,
                message="Analysis unit has no source provenance",
                recovery="Reject the unit or restore its source block references.",
            )
        normalized = re.sub(r"\s+", " ", unit.content).strip().casefold()
        content_hash = hashlib.sha256(unit.content.encode("utf-8")).hexdigest()
        canonical_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        candidates.append(
            AssetCandidate(
                asset_id=unit.unit_id,
                canonical_key=f"{unit.kind}:{canonical_digest}",
                content_sha256=content_hash,
                scope=["general"],
                provenance=provenance,
            )
        )
    return candidates


__all__ = [
    "PackIncrementalPublisher",
    "PackIncrementalResult",
    "asset_candidates_from_analysis",
]
