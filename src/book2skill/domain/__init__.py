"""Public domain exports."""

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.domain.identifiers import derive_block_id, derive_candidate_unit_id
from book2skill.domain.knowledge import (
    ConflictRecord,
    KnowledgeCluster,
    KnowledgeRef,
    KnowledgeUnit,
    ReviewItem,
    UnitKind,
    active_view,
    build_supersession,
    cluster_units,
    current_view,
    detect_conflicts,
    latest_record,
)
from book2skill.domain.models import (
    Confidentiality,
    ExtractionMapEntry,
    Locator,
    LocatorKind,
    SourceFormat,
    SourceManifest,
    TextBlock,
)
from book2skill.domain.state import (
    ConflictStatus,
    ExtractionStatus,
    KnowledgeStatus,
    PublishStatus,
)

__all__ = [
    "SourceManifest",
    "ExtractionMapEntry",
    "TextBlock",
    "Locator",
    "SourceFormat",
    "LocatorKind",
    "Confidentiality",
    "DomainError",
    "ErrorCode",
    "ExtractionStatus",
    "KnowledgeStatus",
    "ConflictStatus",
    "PublishStatus",
    "UnitKind",
    "KnowledgeRef",
    "KnowledgeUnit",
    "ConflictRecord",
    "ReviewItem",
    "KnowledgeCluster",
    "cluster_units",
    "detect_conflicts",
    "latest_record",
    "build_supersession",
    "current_view",
    "active_view",
    "derive_block_id",
    "derive_candidate_unit_id",
]
