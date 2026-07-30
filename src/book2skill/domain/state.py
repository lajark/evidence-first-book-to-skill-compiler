"""State enums for extraction, knowledge, conflicts and publishing."""

from enum import StrEnum


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    PARTIAL = "partial"
    FAILED = "failed"


class KnowledgeStatus(StrEnum):
    CANDIDATE = "candidate"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ConflictStatus(StrEnum):
    OPEN = "open"
    RESOLVED_BY_SCOPE = "resolved_by_scope"
    RESOLVED_BY_USER = "resolved_by_user"
    PRESERVED = "preserved"


class PublishStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    PUBLISHED = "published"
    ROLLED_BACK = "rolled_back"
