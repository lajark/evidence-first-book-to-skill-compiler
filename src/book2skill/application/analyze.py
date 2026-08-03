"""Analyze Only use case (PRD FR-03-1).

Discovers and validates inputs, extracts text blocks, then asks the LLM
adapter to produce structure, candidate units and suggested skills. The
result is an :class:`~book2skill.application.models.AnalysisBundle` — never a
compiled Skill, so a human can review before building.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
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
    ExtractionMapEntry,
    SourceManifest,
    TextBlock,
    derive_candidate_unit_id,
)
from book2skill.extractors.registry import ExtractorRegistry, default_registry
from book2skill.llm.chunking import ChunkItem, chunk_blocks
from book2skill.llm.ports import LLMAdapter
from book2skill.llm.runtime import (
    LLMRuntimeConfig,
    RuntimeLLMAdapter,
    build_llm_adapter,
)
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
        runtime_config: LLMRuntimeConfig | None = None,
        raw_storage: RawStorage | None = None,
        data_home: Path | None = None,
    ) -> None:
        self._gate = gate or Gate()
        self._registry = registry or default_registry()
        if llm is not None and runtime_config is not None:
            raise ValueError("pass either llm or runtime_config, not both")
        runtime = runtime_config or (LLMRuntimeConfig.mock() if llm is None else None)
        if runtime is not None:
            cache_root = (data_home / ".cache" / "llm") if data_home else None
            self._runtime_llm: RuntimeLLMAdapter | None = build_llm_adapter(
                runtime, cache_root=cache_root
            )
            self._llm: LLMAdapter = self._runtime_llm
        elif isinstance(llm, RuntimeLLMAdapter):
            self._runtime_llm = llm
            self._llm = llm
        else:
            assert llm is not None
            self._runtime_llm = None
            self._llm = llm
        self._raw_storage = raw_storage or (
            FileRawStorage(data_home) if data_home else _MemoryRawStorage()
        )

    @property
    def raw_storage(self) -> RawStorage:
        """Return the immutable Raw store used by this analysis instance."""
        return self._raw_storage

    def execute(
        self,
        inputs: list[str],
        *,
        collection_id: str | None = None,
        rights_note: str | None = None,
        on_progress: ProgressReporter | None = None,
        persist_raw: bool = True,
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
            persist_raw: Persist immutable Raw artefacts when ``True``. Update
                planning passes ``False`` so a dry run remains read-only.

        Returns:
            An :class:`AnalyzeResult` with the bundle and/or discovery errors.
        """
        reporter = on_progress or noop_progress
        files, gate_errors = self._gate.discover(inputs)
        return self.execute_discovered(
            files,
            gate_errors=gate_errors,
            collection_id=collection_id,
            rights_note=rights_note,
            on_progress=reporter,
            persist_raw=persist_raw,
        )

    def execute_discovered(
        self,
        files: list[DiscoveredFile],
        *,
        gate_errors: list[GateError] | None = None,
        collection_id: str | None = None,
        rights_note: str | None = None,
        on_progress: ProgressReporter | None = None,
        persist_raw: bool = True,
    ) -> AnalyzeResult:
        """Analyze trusted Gate output without rediscovering or rehashing it.

        Batch orchestration owns its first discovery pass. Reusing its immutable
        :class:`DiscoveredFile` records preserves that gate decision and avoids
        reopening every source merely to recompute an identical SHA-256.
        """
        reporter = on_progress or noop_progress
        errors = list(gate_errors or [])
        if self._runtime_llm is not None:
            self._runtime_llm.start_run()
        if not files:
            return AnalyzeResult(bundle=None, errors=errors)

        # (block, source_id, trusted block_id)
        all_blocks: list[tuple[TextBlock, str, str, str]] = []
        source_ids: list[str] = []
        total_files = len(files)

        for i, f in enumerate(files, start=1):
            extractor = self._registry.get(f.format)
            if extractor is None:
                errors.append(
                    GateError(
                        path=f.path,
                        code=ErrorCode.GATE_UNSUPPORTED_FORMAT,
                        message=f"No extractor for format {f.format.value}",
                        recovery="Register an extractor for this format.",
                    )
                )
                continue

            try:
                extracted = extractor.extract_result(
                    f.path,
                    source_id=f.source_id,
                    source_format=f.format,
                    content_sha256=f.content_sha256,
                    original_name=f.original_name,
                    rights_note=rights_note,
                )
            except DomainError as exc:
                errors.append(
                    GateError(
                        path=f.path,
                        code=exc.code,
                        message=exc.message,
                        recovery=exc.recovery,
                    )
                )
                continue

            # Report extraction completion *after* the work, so the bar's
            # ``completed`` reflects what actually finished rather than
            # jumping to 100% before the (slow) extraction runs.
            reporter(STAGE_EXTRACT, i, total_files, Path(f.path).name)

            if persist_raw:
                self._persist(extracted.manifest, f, extracted.entries)
            source_ids.append(f.source_id)
            extractor_id = (
                f"{type(extractor).__module__}.{type(extractor).__qualname__}"
            )
            for block, entry in zip(extracted.blocks, extracted.entries, strict=True):
                all_blocks.append((block, f.source_id, entry.block_id, extractor_id))

        if not source_ids:
            return AnalyzeResult(bundle=None, errors=errors)

        bundle = self._build_bundle(
            source_ids=source_ids,
            all_blocks=all_blocks,
            collection_id=collection_id,
            on_progress=reporter,
        )
        return AnalyzeResult(bundle=bundle, errors=errors)

    # ---- bundle assembly --------------------------------------------------

    def _build_bundle(
        self,
        *,
        source_ids: list[str],
        all_blocks: list[tuple[TextBlock, str, str, str]],
        collection_id: str | None,
        on_progress: ProgressReporter | None = None,
    ) -> AnalysisBundle:
        reporter = on_progress or noop_progress
        coll_id = collection_id or _derive_collection_id(source_ids)

        chunk_method = getattr(self._llm, "analyze_chunk", None)
        if callable(chunk_method):
            return self._build_chunked_bundle(
                source_ids=source_ids,
                all_blocks=all_blocks,
                collection_id=coll_id,
                on_progress=reporter,
                analyze_chunk=chunk_method,
            )

        structure: list[StructureEntry] = []
        candidates: list[CandidateUnit] = []
        review_queue: list[ReviewItem] = []
        total_blocks = len(all_blocks)
        if total_blocks:
            reporter(STAGE_STRUCTURE, 0, total_blocks, "")
            reporter(STAGE_CANDIDATES, 0, total_blocks, "")

        for idx, (block, source_id, block_id, _extractor_id) in enumerate(
            all_blocks, start=1
        ):
            struct_entries = self._llm.analyze_structure(source_id, [block])
            for s in struct_entries:
                # Tolerate residual schema deviations the adapter could not
                # coerce: skip a single malformed entry rather than crashing
                # the whole pipeline (graceful degradation contract).
                try:
                    trusted_structure = dict(s)
                    trusted_structure["block_id"] = block_id
                    trusted_structure["locator"] = block.locator.model_dump(mode="json")
                    structure.append(StructureEntry.model_validate(trusted_structure))
                except ValidationError:
                    continue
            reporter(STAGE_STRUCTURE, idx, total_blocks, source_id)

            cands = self._llm.extract_candidates(source_id, [block])
            for candidate_idx, c in enumerate(cands, start=1):
                try:
                    trusted_candidate = dict(c)
                    trusted_candidate["unit_id"] = derive_candidate_unit_id(
                        block_id, candidate_idx
                    )
                    trusted_candidate["source_refs"] = [
                        {"source_id": source_id, "block_id": block_id}
                    ]
                    trusted_candidate["record_version"] = 1
                    candidate = CandidateUnit.model_validate(trusted_candidate)
                except ValidationError:
                    continue
                candidates.append(candidate)
                self._maybe_flag(candidate, review_queue)
            reporter(STAGE_CANDIDATES, idx, total_blocks, source_id)

        conflicts = self._detect_conflicts(candidates)

        suggested: list[SuggestedSkill] = []
        if candidates:
            total_sources = len(source_ids)
            if total_sources:
                reporter(STAGE_SKILLS, 0, total_sources, "")
            for idx, source_id in enumerate(source_ids, start=1):
                source_cands = [
                    c.model_dump(mode="json")
                    for c in candidates
                    if any(
                        ref.get("source_id") == source_id for ref in c.source_refs
                    )
                ]
                for suggestion in self._llm.suggest_skills(source_id, source_cands):
                    try:
                        suggested.append(SuggestedSkill.model_validate(suggestion))
                    except ValidationError:
                        continue
                reporter(STAGE_SKILLS, idx, total_sources, source_id)

        return AnalysisBundle(
            collection_id=coll_id,
            source_ids=source_ids,
            structure=structure,
            candidate_units=candidates,
            review_queue=review_queue,
            conflicts=conflicts,
            suggested_skills=suggested,
            analysis_run=(
                self._runtime_llm.manifest
                if self._runtime_llm is not None
                else None
            ),
        )

    def _build_chunked_bundle(
        self,
        *,
        source_ids: list[str],
        all_blocks: list[tuple[TextBlock, str, str, str]],
        collection_id: str,
        on_progress: ProgressReporter,
        analyze_chunk: Callable[
            [str, list[ChunkItem]], dict[str, list[dict[str, object]]]
        ],
    ) -> AnalysisBundle:
        """Use one bounded structure/candidate request per deterministic chunk."""
        grouped: dict[str, list[tuple[TextBlock, str]]] = {
            source_id: [] for source_id in source_ids
        }
        extractor_ids: dict[str, str] = {}
        for block, source_id, block_id, extractor_id in all_blocks:
            grouped[source_id].append((block, block_id))
            extractor_ids.setdefault(source_id, extractor_id)
        chunks = [
            chunk
            for source_id in source_ids
            for chunk in chunk_blocks(source_id, grouped[source_id])
        ]
        structure: list[StructureEntry] = []
        candidates: list[CandidateUnit] = []
        review_queue: list[ReviewItem] = []
        candidate_indices: dict[str, int] = {}
        total_chunks = len(chunks)
        # Mark the structure stage as started *before* the (potentially slow)
        # bulk LLM call so the progress bar leaves the extraction stage's 100%
        # and shows "0 of N" instead of freezing on the previous stage.
        if total_chunks:
            on_progress(STAGE_STRUCTURE, 0, total_chunks, "")
        # The capability is only provided by built-in LLM adapters. Keeping the
        # legacy two-method protocol below preserves narrow test/future adapters.
        chunk_call = analyze_chunk
        if self._runtime_llm is not None:
            responses = self._runtime_llm.analyze_chunks(
                [
                    (
                        chunk.source_id,
                        list(chunk.items),
                        extractor_ids[chunk.source_id],
                    )
                    for chunk in chunks
                ]
            )
        else:
            responses = [
                chunk_call(chunk.source_id, list(chunk.items)) for chunk in chunks
            ]
        responses_by_chunk = zip(chunks, responses, strict=True)
        for index, (chunk, response) in enumerate(responses_by_chunk, start=1):
            on_progress(STAGE_STRUCTURE, index, total_chunks, chunk.source_id)
            if not isinstance(response, dict):
                continue
            item_by_id = {item.input_id: item for item in chunk.items}
            raw_structure = response.get("structure", [])
            raw_candidates = response.get("candidates", [])
            if not isinstance(raw_structure, list) or not isinstance(
                raw_candidates, list
            ):
                continue
            for entry in raw_structure:
                if not isinstance(entry, dict):
                    continue
                input_id = entry.get("input_id")
                item = item_by_id.get(input_id) if isinstance(input_id, str) else None
                if item is None:
                    continue
                try:
                    trusted = dict(entry)
                    trusted.pop("input_id", None)
                    trusted["block_id"] = item.source_block_id
                    trusted["locator"] = item.locator.model_dump(mode="json")
                    structure.append(StructureEntry.model_validate(trusted))
                except ValidationError:
                    continue
            on_progress(STAGE_CANDIDATES, index, total_chunks, chunk.source_id)
            for entry in raw_candidates:
                if not isinstance(entry, dict):
                    continue
                input_id = entry.get("input_id")
                item = item_by_id.get(input_id) if isinstance(input_id, str) else None
                if item is None:
                    continue
                try:
                    candidate_indices[item.source_block_id] = (
                        candidate_indices.get(item.source_block_id, 0) + 1
                    )
                    trusted = dict(entry)
                    trusted.pop("input_id", None)
                    trusted["unit_id"] = derive_candidate_unit_id(
                        item.source_block_id, candidate_indices[item.source_block_id]
                    )
                    trusted["source_refs"] = [
                        {"source_id": chunk.source_id, "block_id": item.source_block_id}
                    ]
                    trusted["record_version"] = 1
                    candidate = CandidateUnit.model_validate(trusted)
                except ValidationError:
                    continue
                candidates.append(candidate)
                self._maybe_flag(candidate, review_queue)

        conflicts = self._detect_conflicts(candidates)
        suggested = self._suggest_skills(source_ids, candidates, on_progress)
        return AnalysisBundle(
            collection_id=collection_id,
            source_ids=source_ids,
            structure=structure,
            candidate_units=candidates,
            review_queue=review_queue,
            conflicts=conflicts,
            suggested_skills=suggested,
            analysis_run=(self._runtime_llm.manifest if self._runtime_llm else None),
        )

    def _suggest_skills(
        self,
        source_ids: list[str],
        candidates: list[CandidateUnit],
        reporter: ProgressReporter,
    ) -> list[SuggestedSkill]:
        suggested: list[SuggestedSkill] = []
        total_sources = len(source_ids)
        # Announce the skills stage before the first (slow) LLM call so the
        # bar does not sit on the candidates stage's 100% while waiting.
        if total_sources:
            reporter(STAGE_SKILLS, 0, total_sources, "")
        for index, source_id in enumerate(source_ids, start=1):
            source_cands = [
                candidate.model_dump(mode="json")
                for candidate in candidates
                if any(
                    ref.get("source_id") == source_id
                    for ref in candidate.source_refs
                )
            ]
            if not source_cands:
                continue
            for suggestion in self._llm.suggest_skills(source_id, source_cands):
                try:
                    suggested.append(SuggestedSkill.model_validate(suggestion))
                except ValidationError:
                    continue
            # Report completion *after* the LLM call for this source.
            reporter(STAGE_SKILLS, index, total_sources, source_id)
        return suggested

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
        entries: tuple[ExtractionMapEntry, ...],
    ) -> None:
        """Persist the manifest and raw original to storage.

        Existing complete versions are immutable. A previously incomplete
        version is repaired by rewriting the same content-addressed inputs.
        """
        if self._raw_storage.exists(manifest.source_id, manifest.version):
            return
        if isinstance(self._raw_storage, FileRawStorage):
            self._raw_storage.save_ingest_from_path(
                manifest,
                f.path,
                entries,
            )
            return
        self._raw_storage.save_original(
            manifest.source_id,
            manifest.version,
            f.path.read_bytes(),
            f.original_name,
        )
        self._raw_storage.save_manifest(manifest)
        self._raw_storage.save_extraction_map(
            manifest.source_id, manifest.version, entries
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
        self._extraction_maps: dict[
            tuple[str, int], list[ExtractionMapEntry]
        ] = {}

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
        entries: Iterable[ExtractionMapEntry],
    ) -> Path:
        self._extraction_maps[(source_id, version)] = list(entries)
        return Path()

    def load_extraction_map(
        self, source_id: str, version: int
    ) -> list[ExtractionMapEntry]:
        return list(self._extraction_maps.get((source_id, version), []))

    def exists(self, source_id: str, version: int) -> bool:
        key = (source_id, version)
        return (
            key in self._manifests
            and key in self._originals
            and key in self._extraction_maps
        )

    def list_versions(self, source_id: str) -> list[int]:
        return [v for (sid, v) in self._manifests if sid == source_id]


__all__ = ["AnalyzeUseCase", "AnalyzeResult"]
