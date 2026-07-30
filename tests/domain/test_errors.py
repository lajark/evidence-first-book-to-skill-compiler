"""Tests for domain errors."""

from book2skill.domain import DomainError, ErrorCode


def test_domain_error_fields() -> None:
    err = DomainError(
        code=ErrorCode.GATE_FILE_NOT_FOUND,
        input_id="src-123",
        message="file not found",
        recovery="check the path",
        details={"path": "/tmp/foo.pdf"},
    )
    assert err.code == ErrorCode.GATE_FILE_NOT_FOUND
    assert err.input_id == "src-123"
    assert err.recovery == "check the path"
    assert err.details == {"path": "/tmp/foo.pdf"}
    assert "src-123" in str(err)
    assert "check the path" in str(err)
