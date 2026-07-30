"""Tests for domain state enums."""

from book2skill.domain import (
    ConflictStatus,
    ExtractionStatus,
    KnowledgeStatus,
    PublishStatus,
)


def test_extraction_status_values() -> None:
    assert ExtractionStatus.PENDING == "pending"
    assert ExtractionStatus.EXTRACTED == "extracted"
    assert ExtractionStatus.PARTIAL == "partial"
    assert ExtractionStatus.FAILED == "failed"


def test_knowledge_status_values() -> None:
    assert KnowledgeStatus.CANDIDATE == "candidate"
    assert KnowledgeStatus.REVIEWED == "reviewed"
    assert KnowledgeStatus.APPROVED == "approved"
    assert KnowledgeStatus.REJECTED == "rejected"
    assert KnowledgeStatus.SUPERSEDED == "superseded"


def test_conflict_status_values() -> None:
    assert ConflictStatus.OPEN == "open"
    assert ConflictStatus.RESOLVED_BY_SCOPE == "resolved_by_scope"
    assert ConflictStatus.RESOLVED_BY_USER == "resolved_by_user"
    assert ConflictStatus.PRESERVED == "preserved"


def test_publish_status_values() -> None:
    assert PublishStatus.DRAFT == "draft"
    assert PublishStatus.VALIDATED == "validated"
    assert PublishStatus.APPROVED == "approved"
    assert PublishStatus.PUBLISHED == "published"
    assert PublishStatus.ROLLED_BACK == "rolled_back"
