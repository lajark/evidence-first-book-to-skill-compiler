"""Storage-specific error types."""

from __future__ import annotations

from pathlib import Path

from book2skill.domain import DomainError, ErrorCode


class StorageError(DomainError):
    """Raised when a storage operation fails."""

    def __init__(
        self,
        code: ErrorCode,
        input_id: str,
        message: str,
        *,
        recovery: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code, input_id, message, recovery=recovery, details=details)


class StoragePathError(StorageError):
    """Raised when a requested path is outside the storage root."""

    def __init__(self, path: Path) -> None:
        super().__init__(
            code=ErrorCode.SCHEMA_VALIDATION_FAILED,
            input_id=str(path),
            message=f"Path traversal attempt: {path}",
            recovery="Use only safe source identifiers and version numbers.",
        )


class StorageNotFoundError(StorageError):
    """Raised when a requested resource is not found."""

    def __init__(self, input_id: str, resource: str) -> None:
        super().__init__(
            code=ErrorCode.GATE_FILE_NOT_FOUND,
            input_id=input_id,
            message=f"Storage resource not found: {resource}",
            recovery="Verify the source identifier and version.",
        )
