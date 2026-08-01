"""Analyze Only use case (PRD FR-03-1).

Discovers and validates inputs, extracts text blocks, then asks the LLM
adapter to produce structure, candidate units and suggested skills. The
result is an :class:`~book2skill.application.models.AnalysisBundle` — never a
compiled Skill, so a human can review before building.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from book2skill.application.gate import DiscoveredFile, Gate, GateError
from book2skill.application.models import (
    AnalysisBundle,
    CandidateUnit,
    ConflictRecord,
    ReviewItem,
    StructureEntry,
    SuggestedSkill,
)
from book2skill.application.progress import (
    STAGE_CANDIDATES,
    STAGE_EXTRACT,
    STAGE_SKILLS,
    STAGE_STRUCTURE,
    ProgressReporter,
    noop_progress,
)
from book2skill.domain import (
    ConflictStatus,
    DomainError,
    ErrorCode,
    SourceManifest,
    TextBlock,
)
from book2skill.extractors.registry import ExtractorRegistry, default_registry
from book2skill.llm.ports import LLMAdapter
from book2skill.storage import FileRawStorage, RawStorage

#: Confidence below which a candidate is auto-flagged for human review.
_LOW_CONFIDENCE_THRESHOLD = 0.5

#: Blocks shorter than this produce a "thin_content" review item.
_THIN_CONTENT_CHARS = 20


@dataclass
class AnalyzeResult:
    """Outcome of an Analyze run.

    On success *bundle* is set and *errors* is empty. When some inputs fail
    discovery or extraction, *bundle* may still be produced from the
    surviving files while *errors* records what was skipped.
    """

    bundle: AnalysisBundle | None = None
    errors: list[GateError] = field(default_factory=list)


class AnalyzeUseCase:
    """Orchestrate the Analyze Only pipeline.

    Dependencies are injected so tests can substitute fakes. The defaults
    (``default_registry``, :class:`~book2skill.llm.MockLLMAdapter`,
    in-memory storage) make the use case work out of the box for M1.
    """

    def __init__(
        self,
        *,
        gate: Gate | None = None,
        registry: ExtractorRegistry | None = None,
        llm: LLMAdapter | None = None,
        raw_storage: RawStorage | None = None,
        data_home: Path | None = None,
    ) -> None:
        self._gate = gate or Gate()
        self._registry = registry or default_registry()
        # Import here to avoid a circular import at module load time.
        from book2skill.llm.mock_adapter import MockLLMAdapter

        self._llm: LLMAdapter = llm or MockLLMAdapter()
        self._raw_storage = raw_storage or (
            FileRawStorage(data_home) if data_home else _MemoryRawStorage()
        )

    def execute(
        self,
        inputs: list[str],
        *,
        collection_id: str | None = None,
        rights_note: str | None = None,
        on_progress: ProgressReporter | None = None,
    ) -> AnalyzeResult:
        """Run the full Analyze pipeline on *inputs*.

        Args:
            inputs: File paths, directories or glob patterns.
            collection_id: Optional collection identifier; auto-derived when
                omitted.
            rights_note: Optional rights-confirmation note recorded on every
                manifest.
            on_progress: Optional stage-progress callback (UI-neutral). When
                omitted the pipeline runs silently.

        Returns:
            An :class:`AnalyzeResult` with the bundle and/or discovery errors.
        """
        reporter = on_progress or noop_progress
        files, gate_errors = self._gate.discover(inputs)
        if not files:
            return AnalyzeResult(bundle=None, errors=gate_errors)

        all_blocks: list[tuple[TextBlock, str]] = []  # (block, source_id)
        source_ids: list[str] = []
        total_files = len(files)

        for i, f in enumerate(files, start=1):
            reporter(STAGE_EXTRACT, i, total_files, Path(f.path).name)
            extractor = self._registry.get(f.format)
            if extractor is None:
                gate_errors.append(
                    GateError(
                        path=f.path,
                        code=ErrorCode.GATE_UNSUPPORTED_FORMAT,
                        message=f"No extractor for format {f.format.value}",
                        recovery="Register an extractor for this format.",
                    )
                )
                continue

            try:
                manifest, entries = extractor.extract(
                    f.path,
                    source_id=f.source_id,
                    rights_note=rights_note,
                )
                blocks = extractor.extract_text_blocks(f.path)
            except DomainError as exc:
                gate_errors.append(
                    GateError(
                        path=f.path,
                        code=exc.code,
                        message=exc.message,
                        recovery=exc.recovery,
                    )
                )
                continue

            self._persist(manifest, f, blocks)
            source_ids.append(f.source_id)
            for block in blocks:
                all_blocks.append((block, f.source_id))

        if not source_ids:
            return AnalyzeResult(bundle=None, errors=gate_errors)

        bundle = self._build_bundle(
            source_ids=source_ids,
            all_blocks=all_blocks,
            collection_id=collection_id,
            on_progress=reporter,
        )
        return AnalyzeResult(bundle=bundle, errors=gate_errors)

    # ---- bundle assembly --------------------------------------------------

    def _build_bundle(
        self,
        *,
        source_ids: list[str],
        all_blocks: list[tuple[TextBlock, str]],
        collection_id: str | None,
        on_progress: ProgressReporter | None = None,
    ) -> AnalysisBundle:
        reporter = on_progress or noop_progress
        coll_id = collection_id or _derive_collection_id(source_ids)

        structure: list[StructureEntry] = []
        candidates: list[CandidateUnit] = []
        review_queue: list[ReviewItem] = []
        total_blocks = len(all_blocks)

        for idx, (block, source_id) in enumerate(all_blocks, start=1):
            reporter(STAGE_STRUCTURE, idx, total_blocks, source_id)
            struct_entries = self._llm.analyze_structure(source_id, [block])
            for s in struct_entries:
                # Tolerate residual schema deviations the adapter could not
                # coerce: skip a single malformed entry rather than crashing
                # the whole pipeline (graceful degradation contract).
                try:
                    structure.append(StructureEntry.model_validate(s))
                except ValidationError:
                    continue

            reporter(STAGE_CANDIDATES, idx, total_blocks, source_id)
            cands = self._llm.extract_candidates(source_id, [block])
            for c in cands:
                try:
                    candidate = CandidateUnit.model_validate(c)
                except ValidationError:
                    continue
                candidates.append(candidate)
                self._maybe_flag(candidate, review_queue)

        conflicts = self._detect_conflicts(candidates)

        suggested: list[SuggestedSkill] = []
        if candidates:
            total_sources = len(source_ids)
            for idx, source_id in enumerate(source_ids, start=1):
                reporter(STAGE_SKILLS, idx, total_sources, source_id)
                source_cands = [
                    c.model_dump(mode="json") for c in candidates
                ]
                for suggestion in self._llm.suggest_skills(source_id, source_cands):
                    try:
                        suggested.append(SuggestedSkill.model_validate(suggestion))
                    except ValidationError:
                        continue

        return AnalysisBundle(
            collection_id=coll_id,
            source_ids=source_ids,
            structure=structure,
            candidate_units=candidates,
            review_queue=review_queue,
            conflicts=conflicts,
            suggested_skills=suggested,
        )

    @staticmethod
    def _maybe_flag(candidate: CandidateUnit, queue: list[ReviewItem]) -> None:
        """Append review items for low-confidence or thin content."""
        if candidate.confidence < _LOW_CONFIDENCE_THRESHOLD:
            queue.append(
                ReviewItem(
                    item_id=f"rv-{candidate.unit_id}-low",
                    ref_type="candidate_unit",
                    ref_id=candidate.unit_id,
                    reason="low_confidence",
                    severity="warning",
                )
            )
        if len(candidate.content) < _THIN_CONTENT_CHARS:
            queue.append(
                ReviewItem(
                    item_id=f"rv-{candidate.unit_id}-thin",
                    ref_type="candidate_unit",
                    ref_id=candidate.unit_id,
                    reason="thin_content",
                    severity="info",
                )
            )

    @staticmethod
    def _detect_conflicts(candidates: list[CandidateUnit]) -> list[ConflictRecord]:
        """Detect duplicate content across candidate units (mock heuristic)."""
        seen: dict[str, list[str]] = {}
        for c in candidates:
            digest = hashlib.sha256(c.content.encode("utf-8")).hexdigest()
            seen.setdefault(digest, []).append(c.unit_id)

        conflicts: list[ConflictRecord] = []
        for idx, (_digest, unit_ids) in enumerate(seen.items()):
            if len(unit_ids) < 2:
                continue
            conflicts.append(
                ConflictRecord(
                    conflict_id=f"conflict-{idx}",
                    unit_ids=unit_ids,
                    description=(
                        f"Duplicate content detected across {len(unit_ids)} "
                        f"candidate unit(s)."
                    ),
                    status=ConflictStatus.OPEN,
                )
            )
        return conflicts

    # ---- persistence ------------------------------------------------------

    def _persist(
        self,
        manifest: SourceManifest,
        f: DiscoveredFile,
        blocks: list[TextBlock],
    ) -> None:
        """Persist the manifest and raw original to storage.

        Text blocks are not persisted here; they live only in the bundle for
        the Analyze stage. Raw immutability is enforced by the storage layer.
        """
        self._raw_storage.save_manifest(manifest)
        if not self._raw_storage.exists(manifest.source_id, manifest.version):
            self._raw_storage.save_original(
                manifest.source_id,
                manifest.version,
                f.path.read_bytes(),
                f.original_name,
            )


def _derive_collection_id(source_ids: list[str]) -> str:
    """Derive a deterministic collection id from the source ids."""
    joined = "|".join(sorted(source_ids))
    return "col-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


class _MemoryRawStorage:
    """Minimal in-memory RawStorage for when no data_home is provided.

    Satisfies the :class:`~book2skill.storage.RawStorage` protocol enough
    for the Analyze use case to persist manifests without touching disk.
    """

    def __init__(self) -> None:
        self._manifests: dict[tuple[str, int], SourceManifest] = {}
        self._originals: dict[tuple[str, int], bytes] = {}
        self._existing: set[tuple[str, int]] = set()

    def save_original(
        self, source_id: str, version: int, data: bytes, original_name: str
    ) -> Path:
        self._originals[(source_id, version)] = data
        return Path(original_name)

    def load_original(self, source_id: str, version: int) -> bytes:
        return self._originals[(source_id, version)]

    def save_manifest(self, manifest: SourceManifest) -> Path:
        self._manifests[(manifest.source_id, manifest.version)] = manifest
        return Path()

    def load_manifest(self, source_id: str, version: int) -> SourceManifest:
        return self._manifests[(source_id, version)]

    def save_extraction_map(
        self,
        source_id: str,
        version: int,
        entries: object,
    ) -> Path:
        return Path()

    def load_extraction_map(self, source_id: str, version: int) -> list[object]:
        return []

    def exists(self, source_id: str, version: int) -> bool:
        return (source_id, version) in self._existing

    def list_versions(self, source_id: str) -> list[int]:
        return [v for (sid, v) in self._manifests if sid == source_id]


__all__ = ["AnalyzeUseCase", "AnalyzeResult"]
