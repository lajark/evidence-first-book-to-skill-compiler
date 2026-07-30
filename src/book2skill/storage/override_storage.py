"""Append-only storage for human-authored field-level overrides.

Layout under ``<data_home>/schema/<collection_id>/``::

    overrides.jsonl   one Override JSON record per line (append-only)

The JSONL mirrors the semantics of :mod:`book2skill.storage.schema_storage`:
records are appended, never rewritten. When a new override supersedes an
existing (unit_id, field) override, the older record is left in place and
the newer one carries a later ``created_at``; :meth:`load_active_overrides`
returns only the latest non-superseded record per (unit_id, field) pair.
"""

from __future__ import annotations

from pathlib import Path

from book2skill.application.diff import Override
from book2skill.storage.file_storage import atomic_write


class OverrideStorage:
    """File-system storage for :class:`Override` records.

    Parameters mirror :class:`~book2skill.storage.KnowledgeSchemaStorage`:
    *data_home* is the root under which ``schema/<collection_id>/`` lives.
    """

    def __init__(self, data_home: Path) -> None:
        self._data_home = data_home.resolve()

    def _collection_dir(self, collection_id: str) -> Path:
        return self._data_home / "schema" / collection_id

    def _overrides_path(self, collection_id: str) -> Path:
        return self._collection_dir(collection_id) / "overrides.jsonl"

    def save_override(self, collection_id: str, override: Override) -> Path:
        """Append one override record as a JSONL line.

        Validates the override's value type before writing so bad overrides
        never reach disk. Append-only: existing lines are never modified.
        """
        override.validate_value()
        path = self._overrides_path(collection_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = override.model_dump_json()
        if path.exists():
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        else:
            atomic_write(path, line + "\n")
        return path

    def load_overrides(self, collection_id: str) -> list[Override]:
        """Load every override record for a collection in file order."""
        path = self._overrides_path(collection_id)
        if not path.exists():
            return []
        overrides: list[Override] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            overrides.append(Override.model_validate_json(line))
        return overrides

    def load_active_overrides(self, collection_id: str) -> list[Override]:
        """Return the latest non-superseded override per (unit_id, field).

        A record is considered superseded if either:
        - Its own ``superseded`` flag is ``True``, or
        - A later record with the same ``override_id`` carries
          ``superseded=True`` (append-only supersede marker).

        For the same (unit_id, field) pair, the record with the latest
        ``created_at`` wins among non-superseded records.
        """
        all_overrides = self.load_overrides(collection_id)
        # Collect override_ids that have at least one superseded=True record.
        superseded_ids: set[str] = {
            o.override_id for o in all_overrides if o.superseded
        }
        latest: dict[tuple[str, str], Override] = {}
        for o in all_overrides:
            if o.superseded or o.override_id in superseded_ids:
                continue
            field_val = o.field.value if hasattr(o.field, "value") else str(o.field)
            key = (o.unit_id, field_val)
            existing = latest.get(key)
            if existing is None or o.created_at > existing.created_at:
                latest[key] = o
        return list(latest.values())

    def load_for_unit(
        self, collection_id: str, unit_id: str
    ) -> list[Override]:
        """Return active overrides targeting *unit_id* only."""
        return [
            o
            for o in self.load_active_overrides(collection_id)
            if o.unit_id == unit_id
        ]

    def supersede_override(
        self,
        collection_id: str,
        override_id: str,
        *,
        reviewer: str = "system",
    ) -> Path:
        """Mark an existing override as superseded by appending a record.

        Append-only: the original override line is not modified. Instead a
        new line is appended carrying ``superseded=True`` and the same
        ``override_id`` so loaders can filter it out. This keeps the file
        history intact.
        """
        existing = next(
            (
                o
                for o in self.load_overrides(collection_id)
                if o.override_id == override_id
            ),
            None,
        )
        if existing is None:
            from book2skill.storage.errors import StorageNotFoundError

            raise StorageNotFoundError(
                override_id, str(self._overrides_path(collection_id))
            )
        superseded = existing.model_copy(
            update={
                "superseded": True,
                "reviewer": reviewer,
                "created_at": existing.created_at,
            }
        )
        path = self._overrides_path(collection_id)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(superseded.model_dump_json() + "\n")
        return path


__all__ = ["OverrideStorage"]
