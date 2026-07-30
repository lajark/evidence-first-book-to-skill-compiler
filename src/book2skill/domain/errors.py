"""Stable error types and codes for the domain layer."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Stable domain error codes.

    Format: <STAGE>_<REASON>.
    """

    # Input / Gate stage
    GATE_FILE_NOT_FOUND = "GATE_FILE_NOT_FOUND"
    GATE_EMPTY_FILE = "GATE_EMPTY_FILE"
    GATE_TOO_LARGE = "GATE_TOO_LARGE"
    GATE_UNSUPPORTED_FORMAT = "GATE_UNSUPPORTED_FORMAT"
    GATE_DAMAGED_FILE = "GATE_DAMAGED_FILE"
    GATE_ENCRYPTED_FILE = "GATE_ENCRYPTED_FILE"
    GATE_RIGHTS_NOT_CONFIRMED = "GATE_RIGHTS_NOT_CONFIRMED"

    # Extraction stage
    EXTRACT_UNSUPPORTED = "EXTRACT_UNSUPPORTED"
    EXTRACT_PARTIAL_FAILURE = "EXTRACT_PARTIAL_FAILURE"

    # Schema / domain validation
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"

    # Compiler stage
    BUILD_IR_FAILED = "BUILD_IR_FAILED"
    BUILD_BUDGET_EXCEEDED = "BUILD_BUDGET_EXCEEDED"
    BUILD_INPUT_INVALID = "BUILD_INPUT_INVALID"

    # Diff / merge stage (TASK-014)
    DIFF_INPUT_INVALID = "DIFF_INPUT_INVALID"
    MERGE_CONFLICT_UNRESOLVABLE = "MERGE_CONFLICT_UNRESOLVABLE"

    # Publish stage (TASK-015)
    PUBLISH_FAILED = "PUBLISH_FAILED"
    PUBLISH_ROLLBACK_FAILED = "PUBLISH_ROLLBACK_FAILED"

    # Validation stage (TASK-016)
    VALIDATE_INPUT_INVALID = "VALIDATE_INPUT_INVALID"
    VALIDATE_SKILL_DIR_INVALID = "VALIDATE_SKILL_DIR_INVALID"

    # Installation stage (TASK-017)
    INSTALL_FAILED = "INSTALL_FAILED"
    INSTALL_SKILL_DIR_INVALID = "INSTALL_SKILL_DIR_INVALID"
    UNINSTALL_FAILED = "UNINSTALL_FAILED"


class DomainError(Exception):
    """Base domain exception with stable code, input id and recovery hint."""

    def __init__(
        self,
        code: ErrorCode,
        input_id: str,
        message: str,
        *,
        recovery: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.input_id = input_id
        self.message = message
        self.recovery = recovery
        self.details = details or {}

    def __str__(self) -> str:
        return (
            f"[{self.code}] input={self.input_id}: {super().__str__()} "
            f"(recovery: {self.recovery})"
        )
