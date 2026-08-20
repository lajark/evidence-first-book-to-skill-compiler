"""Tests for the Update transaction lifecycle guard."""

from pathlib import Path

import pytest

from book2skill.application.update_transaction import (
    UpdateTransactionStore,
    can_transition,
)
from book2skill.domain.errors import DomainError, ErrorCode


def test_transaction_lifecycle_allows_publish_and_recovery_paths(
    tmp_path: Path,
) -> None:
    store = UpdateTransactionStore(tmp_path / "data")
    transaction = store.begin("collection", tmp_path / "skill")

    assert can_transition("prepared", "publishing")
    transaction = store.update(transaction, state="publishing")
    transaction = store.update(transaction, state="skill_published")
    transaction = store.update(transaction, state="schema_committed")
    transaction = store.update(transaction, state="committed")

    assert transaction.state == "committed"
    store.remove(transaction)


def test_transaction_lifecycle_rejects_state_skip(
    tmp_path: Path,
) -> None:
    store = UpdateTransactionStore(tmp_path / "data")
    transaction = store.begin("collection", tmp_path / "skill")

    with pytest.raises(DomainError) as exc_info:
        store.update(transaction, state="committed")

    error = exc_info.value
    assert error.code == ErrorCode.INVALID_STATE_TRANSITION
    assert error.details == {
        "transaction_id": transaction.transaction_id,
        "from_state": "prepared",
        "to_state": "committed",
    }
    assert transaction.state == "prepared"


def test_transaction_lifecycle_allows_rollback_required_from_incomplete_state(
    tmp_path: Path,
) -> None:
    store = UpdateTransactionStore(tmp_path / "data")
    transaction = store.begin("collection", tmp_path / "skill")
    transaction = store.update(transaction, state="publishing")

    transaction = store.update(transaction, state="rollback_required")

    assert transaction.state == "rollback_required"
    assert not can_transition("rollback_required", "prepared")
    store.remove(transaction)
