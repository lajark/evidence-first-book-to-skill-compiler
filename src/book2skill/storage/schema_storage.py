"""Typed Schema-layer storage for knowledge units.

Extends the generic :class:`~book2skill.storage.FileSchemaStorage` (which
handles ``collection.json`` documents) with knowledge-unit persistence to
``units.jsonl``. The JSONL is **append-only**: corrections supersede the
current record by appending a new one with a higher ``record_version`` and a
``supersedes`` link, never by rewriting or deleting an existing line. This
preserves the full history and satisfies the no-in-place-erasure invariant.
"""

from __future__ import annotations

from pathlib import Path

from book2skill.domain.knowledge import (
    KnowledgeUnit,
    UnitKind,
    build_supersession,
    latest_record,
)

from .errors import StorageNotFoundError
from .file_storage import FileSchemaStorage, atomic_write


class KnowledgeSchemaStorage(FileSchemaStorage):
    """File-system Schema storage with typed knowledge-unit operations.

    Layout under ``<data_home>/schema/<collection_id>/``::

        collection.json   collection metadata (generic, via parent)
        units.jsonl        one KnowledgeUnit JSON record per line (append-only)
    """

    def _units_path(self, collection_id: str) -> Path:
        return self._collection_dir(collection_id) / "units.jsonl"

    def save_unit(self, collection_id: str, unit: KnowledgeUnit) -> Path:
        """Append one knowledge unit as a JSONL line.

        Appending (rather than rewriting) is deliberate: it preserves every
        record ever written, so supersession history stays intact.
        """
        path = self._units_path(collection_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = unit.model_dump_json()
        if path.exists():
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        else:
            atomic_write(path, line + "\n")
        return path

    def save_units_atomic(
        self, collection_id: str, units: list[KnowledgeUnit]
    ) -> Path:
        """Atomically append a batch of knowledge-unit records.

        The complete post-append JSONL content is assembled before the target
        file is replaced. If serialisation or the atomic replacement fails,
        the previously persisted history remains unchanged. An empty batch is
        a no-op and does not create the collection directory or units file.
        """
        path = self._units_path(collection_id)
        if not units:
            return path

        additions = "".join(f"{unit.model_dump_json()}\n" for unit in units)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, existing + additions)
        return path

    def save_new_units_atomic(
        self, collection_id: str, units: list[KnowledgeUnit]
    ) -> Path:
        """Append only records not already present in append-only history.

        Full Build may be repeated for an unchanged source collection. The
        current view alone is insufficient for this test because a historical
        record can still be the exact replayed Build result after a later
        update. Comparing canonical Pydantic JSON avoids duplicate history
        while leaving Update responsible for intentional supersessions.
        """
        existing = {unit.model_dump_json() for unit in self.load_units(collection_id)}
        additions: list[KnowledgeUnit] = []
        for unit in units:
            encoded = unit.model_dump_json()
            if encoded in existing:
                continue
            existing.add(encoded)
            additions.append(unit)
        return self.save_units_atomic(collection_id, additions)

    def load_units(self, collection_id: str) -> list[KnowledgeUnit]:
        """Load every knowledge-unit record for a collection.

        Returns records in file order; callers needing the "current" view per
        unit should use :meth:`load_unit` (latest record) or filter
        :func:`~book2skill.domain.knowledge.latest_record` themselves.
        """
        path = self._units_path(collection_id)
        if not path.exists():
            return []
        units: list[KnowledgeUnit] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            units.append(KnowledgeUnit.model_validate_json(line))
        return units

    def load_unit(
        self, collection_id: str, unit_id: str
    ) -> KnowledgeUnit | None:
        """Return the latest record for *unit_id*, or ``None`` if absent."""
        return latest_record(self.load_units(collection_id), unit_id)

    def load_unit_history(
        self, collection_id: str, unit_id: str
    ) -> list[KnowledgeUnit]:
        """Return all records for *unit_id* ordered by ``record_version``."""
        records = [
            u for u in self.load_units(collection_id) if u.unit_id == unit_id
        ]
        return sorted(records, key=lambda u: u.record_version)

    def supersede_unit(
        self, collection_id: str, unit_id: str, new_unit: KnowledgeUnit
    ) -> KnowledgeUnit:
        """Append a new record superseding the current one for *unit_id*.

        The latest existing record is left untouched (append-only); only the
        new record is written, with bumped ``record_version`` and
        ``supersedes`` set by :func:`build_supersession`. Returns the persisted
        new record. Raises ``StorageNotFoundError`` if *unit_id* is unknown.
        """
        old = self.load_unit(collection_id, unit_id)
        if old is None:
            raise StorageNotFoundError(
                unit_id, str(self._units_path(collection_id))
            )
        successor = build_supersession(old, new_unit)
        self.save_unit(collection_id, successor)
        return successor

    def load_by_kind(
        self, collection_id: str, kind: UnitKind
    ) -> list[KnowledgeUnit]:
        """Return the latest record per unit of a given ``kind``.

        This provides the "frameworks" / "principles" / ... view as a filter
        over the canonical ``units.jsonl`` rather than a separate file,
        avoiding dual-write divergence.
        """
        kind_value = kind.value if isinstance(kind, UnitKind) else str(kind)
        units = self.load_units(collection_id)
        seen: dict[str, KnowledgeUnit] = {}
        for u in units:
            if u.kind != kind_value:
                continue
            current = seen.get(u.unit_id)
            if current is None or u.record_version > current.record_version:
                seen[u.unit_id] = u
        return list(seen.values())


__all__ = ["KnowledgeSchemaStorage"]
