"""Candidate-to-production Asset Pack update sample for B2S-M13-03.

The publisher is deliberately pure and in-memory.  It demonstrates the
governance boundary before the existing Build/Publisher path is migrated:
candidate assets must be reviewed, compatible assets are merged without
changing the Kernel, conflicts block publication, and a prior Pack remains a
valid rollback target.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.runtime.closure import ProvenanceRef

_SHA256_PATTERN = r"^[a-f0-9]{64}$"


class PackStage(StrEnum):
    """Governance state for candidate and production Pack records."""

    CANDIDATE = "candidate"
    REVIEW = "review"
    APPROVED = "approved"
    PRODUCTION = "production"
    REJECTED = "rejected"


class AssetCandidate(BaseModel):
    """A source-scoped asset awaiting human review."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(..., min_length=1)
    canonical_key: str = Field(..., min_length=1)
    content_sha256: str = Field(..., pattern=_SHA256_PATTERN)
    scope: list[str] = Field(..., min_length=1)
    provenance: list[ProvenanceRef] = Field(..., min_length=1)


class AssetRecord(BaseModel):
    """An approved asset in a published Pack."""

    model_config = ConfigDict(extra="forbid")

    canonical_key: str = Field(..., min_length=1)
    content_sha256: str = Field(..., pattern=_SHA256_PATTERN)
    scope: list[str] = Field(..., min_length=1)
    provenance: list[ProvenanceRef] = Field(..., min_length=1)


class ReviewDecision(BaseModel):
    """Explicit human decision for one candidate asset."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(..., min_length=1)
    decision: Literal["approve", "reject"]
    reviewer: str = Field(..., min_length=1)
    reason: str | None = None


class PackCandidate(BaseModel):
    """A candidate batch isolated from the production Pack pointer."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(..., min_length=1)
    task_contract_id: str = Field(..., min_length=1)
    task_contract_version: str = Field(..., min_length=1)
    skill_kernel_id: str = Field(..., min_length=1)
    skill_kernel_version: str = Field(..., min_length=1)
    skill_kernel_sha256: str = Field(..., pattern=_SHA256_PATTERN)
    base_pack_id: str = Field(..., min_length=1)
    base_pack_version: str = Field(..., min_length=1)
    assets: list[AssetCandidate] = Field(..., min_length=1)
    stage: PackStage = PackStage.CANDIDATE
    decisions: dict[str, ReviewDecision] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_decisions(self) -> Self:
        asset_ids = {asset.asset_id for asset in self.assets}
        if len(asset_ids) != len(self.assets):
            raise ValueError("candidate asset_id values must be unique")
        if not set(self.decisions).issubset(asset_ids):
            raise ValueError("review decision references an unknown candidate asset")
        return self


class AssetPackRelease(BaseModel):
    """Immutable production Pack release with a deterministic content hash."""

    model_config = ConfigDict(extra="forbid")

    pack_id: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    task_contract_id: str = Field(..., min_length=1)
    task_contract_version: str = Field(..., min_length=1)
    skill_kernel_id: str = Field(..., min_length=1)
    skill_kernel_version: str = Field(..., min_length=1)
    skill_kernel_sha256: str = Field(..., pattern=_SHA256_PATTERN)
    assets: list[AssetRecord] = Field(default_factory=list)
    stage: Literal["production"] = "production"
    previous_version: str | None = None
    pack_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"pack_hash"})

    def compute_hash(self) -> str:
        payload = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def with_hash(self) -> Self:
        return self.model_copy(update={"pack_hash": self.compute_hash()})


class PackUpdateError(DomainError):
    """Stable error raised when Pack governance cannot proceed."""

    def __init__(
        self,
        code: ErrorCode,
        task_contract_id: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code,
            task_contract_id,
            message,
            recovery=(
                "Keep the production Pack unchanged and resolve the candidate issue."
            ),
            details=details,
        )


@dataclass(frozen=True)
class PackRollback:
    """Auditable selection of a prior immutable Pack release."""

    from_version: str
    to_version: str
    selected_pack: AssetPackRelease


def _error(
    task_contract_id: str,
    code: ErrorCode,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> PackUpdateError:
    return PackUpdateError(code, task_contract_id, message, details=details)


def _version_tuple(version: str) -> tuple[int, int, int]:
    try:
        parts = tuple(int(part) for part in version.split("."))
    except ValueError as exc:
        raise ValueError("Pack versions must use numeric x.y.z form") from exc
    if len(parts) != 3 or any(part < 0 for part in parts):
        raise ValueError("Pack versions must use numeric x.y.z form")
    return parts


def _merge_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _merge_provenance(
    first: Iterable[ProvenanceRef], second: Iterable[ProvenanceRef]
) -> list[ProvenanceRef]:
    seen: set[tuple[str, str, str | None]] = set()
    merged: list[ProvenanceRef] = []
    for item in (*first, *second):
        key = (item.source_id, item.locator, item.source_sha256)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


class PackPublisher:
    """Pure candidate/review/merge/regression/publish workflow."""

    def create_candidate(
        self,
        base: AssetPackRelease,
        *,
        candidate_id: str,
        assets: list[AssetCandidate],
    ) -> PackCandidate:
        return PackCandidate(
            candidate_id=candidate_id,
            task_contract_id=base.task_contract_id,
            task_contract_version=base.task_contract_version,
            skill_kernel_id=base.skill_kernel_id,
            skill_kernel_version=base.skill_kernel_version,
            skill_kernel_sha256=base.skill_kernel_sha256,
            base_pack_id=base.pack_id,
            base_pack_version=base.version,
            assets=assets,
        )

    def review(
        self,
        candidate: PackCandidate,
        decisions: Iterable[ReviewDecision],
    ) -> PackCandidate:
        decision_map = {decision.asset_id: decision for decision in decisions}
        expected = {asset.asset_id for asset in candidate.assets}
        if set(decision_map) != expected:
            raise _error(
                candidate.task_contract_id,
                ErrorCode.PACK_UPDATE_REVIEW_REQUIRED,
                "Every candidate asset requires an explicit review decision",
                details={"missing": sorted(expected - set(decision_map))},
            )
        return candidate.model_copy(
            update={"stage": PackStage.REVIEW, "decisions": decision_map}
        )

    def approve(self, candidate: PackCandidate) -> PackCandidate:
        """Close review and move only unanimously approved assets forward."""

        if candidate.stage != PackStage.REVIEW:
            raise _error(
                candidate.task_contract_id,
                ErrorCode.PACK_UPDATE_REVIEW_REQUIRED,
                "Pack candidate must complete review before approval",
                details={"stage": candidate.stage},
            )
        stage = (
            PackStage.APPROVED
            if all(item.decision == "approve" for item in candidate.decisions.values())
            else PackStage.REJECTED
        )
        return candidate.model_copy(update={"stage": stage})

    def publish(
        self,
        base: AssetPackRelease,
        candidate: PackCandidate,
        *,
        version: str,
        regression: Callable[[AssetPackRelease], bool],
    ) -> AssetPackRelease:
        if base.pack_hash != base.compute_hash():
            raise _error(
                base.task_contract_id,
                ErrorCode.PACK_UPDATE_INVALID,
                "Current production Pack hash is stale",
                details={"pack_id": base.pack_id, "version": base.version},
            )
        if candidate.stage != PackStage.APPROVED:
            raise _error(
                candidate.task_contract_id,
                ErrorCode.PACK_UPDATE_REVIEW_REQUIRED,
                "Only an approved candidate can enter a production Pack",
                details={"stage": candidate.stage},
            )
        if (
            candidate.base_pack_id != base.pack_id
            or candidate.base_pack_version != base.version
            or candidate.task_contract_id != base.task_contract_id
            or candidate.task_contract_version != base.task_contract_version
            or candidate.skill_kernel_id != base.skill_kernel_id
            or candidate.skill_kernel_version != base.skill_kernel_version
            or candidate.skill_kernel_sha256 != base.skill_kernel_sha256
        ):
            raise _error(
                candidate.task_contract_id,
                ErrorCode.PACK_UPDATE_CONTRACT_MISMATCH,
                "Candidate is not compatible with the current Pack/Kernel",
            )
        try:
            if _version_tuple(version) <= _version_tuple(base.version):
                raise ValueError("new Pack version must be greater than base version")
        except ValueError as exc:
            raise _error(
                candidate.task_contract_id,
                ErrorCode.PACK_UPDATE_INVALID,
                str(exc),
                details={"base_version": base.version, "new_version": version},
            ) from exc

        merged: dict[str, AssetRecord] = {
            asset.canonical_key: asset for asset in base.assets
        }
        for asset in candidate.assets:
            if candidate.decisions[asset.asset_id].decision != "approve":
                continue
            existing = merged.get(asset.canonical_key)
            if existing is None:
                merged[asset.canonical_key] = AssetRecord(
                    canonical_key=asset.canonical_key,
                    content_sha256=asset.content_sha256,
                    scope=asset.scope,
                    provenance=asset.provenance,
                )
                continue
            if existing.content_sha256 != asset.content_sha256:
                raise _error(
                    candidate.task_contract_id,
                    ErrorCode.PACK_UPDATE_CONFLICT,
                    f"Conflicting content for asset key {asset.canonical_key}",
                    details={
                        "canonical_key": asset.canonical_key,
                        "existing_hash": existing.content_sha256,
                        "candidate_hash": asset.content_sha256,
                    },
                )
            merged[asset.canonical_key] = existing.model_copy(
                update={
                    "scope": _merge_unique((*existing.scope, *asset.scope)),
                    "provenance": _merge_provenance(
                        existing.provenance, asset.provenance
                    ),
                }
            )

        release = AssetPackRelease(
            pack_id=base.pack_id,
            version=version,
            task_contract_id=base.task_contract_id,
            task_contract_version=base.task_contract_version,
            skill_kernel_id=base.skill_kernel_id,
            skill_kernel_version=base.skill_kernel_version,
            skill_kernel_sha256=base.skill_kernel_sha256,
            assets=list(merged.values()),
            previous_version=base.version,
        ).with_hash()
        if not regression(release):
            raise _error(
                candidate.task_contract_id,
                ErrorCode.PACK_UPDATE_REGRESSION_FAILED,
                "Pack regression callback rejected the candidate release",
                details={"version": version},
            )
        return release

    def rollback(
        self, current: AssetPackRelease, target: AssetPackRelease
    ) -> PackRollback:
        if (
            current.pack_id != target.pack_id
            or current.task_contract_id != target.task_contract_id
            or current.skill_kernel_id != target.skill_kernel_id
            or current.skill_kernel_sha256 != target.skill_kernel_sha256
            or target.stage != "production"
            or target.pack_hash != target.compute_hash()
        ):
            raise _error(
                current.task_contract_id,
                ErrorCode.PACK_UPDATE_ROLLBACK_INVALID,
                "Rollback target is not compatible with the current Kernel",
            )
        return PackRollback(
            from_version=current.version,
            to_version=target.version,
            selected_pack=target,
        )
