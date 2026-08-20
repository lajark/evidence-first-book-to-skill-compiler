"""Crash-recoverable journal for the Update Skill/Schema transaction."""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from book2skill.domain.errors import DomainError, ErrorCode
from book2skill.storage import atomic_write

TransactionState = Literal[
    "prepared",
    "publishing",
    "skill_published",
    "schema_committed",
    "committed",
    "rollback_required",
]

# The update journal is a durable state machine.  Keeping the graph next to
# the journal writer makes every state mutation (including recovery) pass
# through the same guard instead of relying on callers to remember the
# lifecycle ordering.
TRANSACTION_TRANSITIONS: dict[TransactionState, frozenset[TransactionState]] = {
    "prepared": frozenset({"prepared", "publishing", "rollback_required"}),
    "publishing": frozenset(
        {"publishing", "skill_published", "committed", "rollback_required"}
    ),
    "skill_published": frozenset(
        {"skill_published", "schema_committed", "committed", "rollback_required"}
    ),
    "schema_committed": frozenset(
        {"schema_committed", "committed", "rollback_required"}
    ),
    "committed": frozenset({"committed"}),
    "rollback_required": frozenset({"rollback_required"}),
}


def can_transition(
    current: TransactionState, target: TransactionState
) -> bool:
    """Return whether *target* is a legal next state for *current*."""

    return target in TRANSACTION_TRANSITIONS[current]


class UpdateTransaction(BaseModel):
    """Durable state for one Update publish/Schema commit attempt."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    transaction_id: str
    state: TransactionState
    collection_id: str
    skill_dir: str
    created_at: str
    pending_records: list[tuple[str, int]] = Field(default_factory=list)
    published_at: str | None = None
    snapshot_path: str | None = None
    artifact_id: str | None = None
    publish_id: str | None = None
    schema_snapshot_path: str | None = None
    schema_existed: bool = True
    schema_sha256: str | None = None
    active_pointer_sha256: str | None = None
    publish_index_sha256: str | None = None


class UpdateTransactionStore:
    """Persist Update journals under the data home using atomic writes."""

    def __init__(self, data_home: Path) -> None:
        self._data_home = data_home.resolve()
        self._root = self._data_home / ".transactions" / "update"

    def begin(
        self,
        collection_id: str,
        skill_dir: Path,
        pending_records: list[tuple[str, int]] | None = None,
    ) -> UpdateTransaction:
        transaction_id = uuid.uuid4().hex
        transaction = UpdateTransaction(
            transaction_id=transaction_id,
            state="prepared",
            collection_id=collection_id,
            skill_dir=str(Path(skill_dir).resolve()),
            created_at=_dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            pending_records=pending_records or [],
            schema_snapshot_path=str(
                self._schema_snapshot_path(transaction_id)
            ),
        )
        schema_path = self._schema_path(collection_id)
        schema_snapshot_path = transaction.schema_snapshot_path
        assert schema_snapshot_path is not None
        schema_snapshot = Path(schema_snapshot_path)
        schema_snapshot.parent.mkdir(parents=True, exist_ok=True)
        existed = schema_path.is_file()
        atomic_write(
            schema_snapshot,
            schema_path.read_text(encoding="utf-8") if existed else "",
        )
        transaction = transaction.model_copy(update={"schema_existed": existed})
        self.save(transaction)
        return transaction

    def save(self, transaction: UpdateTransaction) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        # Tests and recovery tooling may assign a stable transaction id after
        # creating a record.  Re-home the durable Schema snapshot with that
        # id so removing the abandoned journal cannot delete live rollback
        # material for the renamed transaction.
        snapshot_path = transaction.schema_snapshot_path
        expected_snapshot = self._schema_snapshot_path(transaction.transaction_id)
        if snapshot_path:
            source_snapshot = Path(snapshot_path)
            if source_snapshot.exists() and source_snapshot != expected_snapshot:
                atomic_write(
                    expected_snapshot,
                    source_snapshot.read_text(encoding="utf-8"),
                )
                with suppress(FileNotFoundError):
                    source_snapshot.unlink()
                transaction = transaction.model_copy(
                    update={"schema_snapshot_path": str(expected_snapshot)}
                )
        atomic_write(
            self._root / f"{transaction.transaction_id}.json",
            transaction.model_dump_json(indent=2),
        )

    def update(
        self,
        transaction: UpdateTransaction,
        *,
        state: TransactionState,
        published_at: str | None = None,
        snapshot_path: str | None = None,
        artifact_id: str | None = None,
        publish_id: str | None = None,
        schema_sha256: str | None = None,
        active_pointer_sha256: str | None = None,
        publish_index_sha256: str | None = None,
    ) -> UpdateTransaction:
        if not can_transition(transaction.state, state):
            raise DomainError(
                code=ErrorCode.INVALID_STATE_TRANSITION,
                input_id=transaction.collection_id,
                message=(
                    "Update transaction state transition is not permitted: "
                    f"{transaction.state} -> {state}."
                ),
                recovery=(
                    "Inspect the transaction journal and resume from its "
                    "current lifecycle state."
                ),
                details={
                    "transaction_id": transaction.transaction_id,
                    "from_state": transaction.state,
                    "to_state": state,
                },
            )
        updated = transaction.model_copy(
            update={
                "state": state,
                "published_at": published_at
                if published_at is not None
                else transaction.published_at,
                "snapshot_path": snapshot_path
                if snapshot_path is not None
                else transaction.snapshot_path,
                "artifact_id": artifact_id
                if artifact_id is not None
                else transaction.artifact_id,
                "publish_id": publish_id
                if publish_id is not None
                else transaction.publish_id,
                "schema_sha256": schema_sha256
                if schema_sha256 is not None
                else transaction.schema_sha256,
                "active_pointer_sha256": active_pointer_sha256
                if active_pointer_sha256 is not None
                else transaction.active_pointer_sha256,
                "publish_index_sha256": publish_index_sha256
                if publish_index_sha256 is not None
                else transaction.publish_index_sha256,
            }
        )
        self.save(updated)
        return updated

    def remove(self, transaction: UpdateTransaction) -> None:
        path = self._root / f"{transaction.transaction_id}.json"
        with suppress(FileNotFoundError):
            path.unlink()
        if transaction.schema_snapshot_path:
            with suppress(FileNotFoundError):
                Path(transaction.schema_snapshot_path).unlink()

    def _schema_path(self, collection_id: str) -> Path:
        return self._data_home / "schema" / collection_id / "units.jsonl"

    def _schema_snapshot_path(self, transaction_id: str) -> Path:
        return self._root / f"{transaction_id}.schema.jsonl"

    def pending(self) -> list[UpdateTransaction]:
        if not self._root.exists():
            return []
        records: list[UpdateTransaction] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                records.append(
                    UpdateTransaction.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, ValueError, json.JSONDecodeError):
                # A truncated journal is left in place for manual diagnosis;
                # it must never be silently interpreted as a committed update.
                continue
        return records


__all__ = [
    "TRANSACTION_TRANSITIONS",
    "TransactionState",
    "UpdateTransaction",
    "UpdateTransactionStore",
    "can_transition",
]
