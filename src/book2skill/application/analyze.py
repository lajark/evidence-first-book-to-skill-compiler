"""Analyze Only use case (PRD FR-03-1).

Discovers and validates inputs, extracts text blocks, then asks the LLM
adapter to produce structure, candidate units and suggested skills. The
result is an :class:`~book2skill.application.models.AnalysisBundle` — never a
compiled Skill, so a human can review before building.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    ANALYZE_STAGE_ORDER,
    STAGE_CANDIDATES,
    STAGE_EXTRACT,
    STAGE_SKILLS,
    STAGE_STRUCTURE,
    STAGE_SYNTHESIS,
    ProgressEvent,
    ProgressReporter,
    emit_progress,
    noop_progress,
    weighted_progress,
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
from book2skill.extractors.base import (
    ExtractionContractError,
    validate_extraction_result,
)
from book2skill.extractors.registry import ExtractorRegistry, default_registry
from book2skill.llm.chunking import ChunkItem, chunk_blocks, estimate_tokens
from book2skill.llm.ports import LLMAdapter
from book2skill.llm.quality import QualityService
from book2skill.llm.router import RouterLLMAdapter
from book2skill.llm.runtime import (
    ChunkProgressEvent,
    LLMRuntimeConfig,
    RuntimeLLMAdapter,
    build_llm_adapter,
)
from book2skill.storage import FileRawStorage, RawStorage

#: Confidence below which a candidate is auto-flagged for human review.
_LOW_CONFIDENCE_THRESHOLD = 0.5

#: Blocks shorter than this produce a "thin_content" review item.
_THIN_CONTENT_CHARS = 20


def _estimated_fraction(elapsed_seconds: float, predicted_seconds: float) -> float:
    """Project non-streaming work monotonically, never claiming completion."""
    if elapsed_seconds <= 0 or predicted_seconds <= 0:
        return 0.0
    return min(0.95, 1.0 - math.exp(-elapsed_seconds / predicted_seconds))


def _remaining_duration(predicted_seconds: float, elapsed_seconds: float) -> float:
    if elapsed_seconds <= 0:
        return predicted_seconds
    return max(
        1.0,
        predicted_seconds
        * math.exp(-elapsed_seconds / max(predicted_seconds, 1.0)),
    )


def _parallel_makespan(durations: list[float], concurrency: int) -> float:
    if not durations:
        return 0.0
    lanes = max(1, min(concurrency, len(durations)))
    return max(max(durations), sum(durations) / lanes)


def _apply_quality_review(
    bundle: AnalysisBundle, quality: QualityService
) -> AnalysisBundle:
    """Run the selective quality review and attach its patches to the bundle.

    Only flagged candidates are reviewed; the Arbiter's disposition updates
    each unit's ``review_status`` and content (merge), and the resulting
    ``ReviewPatch`` list is attached to the bundle. When no candidate is
    flagged, the bundle is returned unchanged (zero extra LLM calls).
    """
    result = quality.review(bundle)
    if not result.patches:
        return bundle
    units = {unit.unit_id: unit for unit in bundle.candidate_units}
    for patch in result.patches:
        unit = units.get(patch.unit_id)
        if unit is None:
            continue
        if patch.disposition == "reject":
            units[patch.unit_id] = unit.model_copy(
                update={"review_status": "rejected"}
            )
        elif patch.disposition == "merge":
            units[patch.unit_id] = unit.model_copy(
                update={
                    "content": patch.revised_content or unit.content,
                    "review_status": "reviewed",
                }
            )
        else:  # accept
            units[patch.unit_id] = unit.model_copy(
                update={"review_status": "reviewed"}
            )
    return bundle.model_copy(
        update={
            "candidate_units": [
                units[unit.unit_id] for unit in bundle.candidate_units
            ],
            "quality_review": result.patches,
        }
    )


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
            self._runtime_llm: RuntimeLLMAdapter | RouterLLMAdapter | None = (
                build_llm_adapter(runtime, cache_root=cache_root)
            )
            self._llm: LLMAdapter = self._runtime_llm
        elif isinstance(llm, (RuntimeLLMAdapter, RouterLLMAdapter)):
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
        quality: QualityService | None = None,
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
            quality: Optional selective quality reviewer (OPT-P1-10). When
                present, flagged candidates are reviewed by Critic/Arbiter and
                the bundle carries the resulting :class:`ReviewPatch` list.

        Returns:
            An :class:`AnalyzeResult` with the bundle and/or discovery errors.
        """
        reporter = weighted_progress(
            on_progress or noop_progress,
            stages=ANALYZE_STAGE_ORDER,
        )
        files, gate_errors = self._gate.discover(inputs)
        return self.execute_discovered(
            files,
            gate_errors=gate_errors,
            collection_id=collection_id,
            rights_note=rights_note,
            on_progress=reporter,
            persist_raw=persist_raw,
            quality=quality,
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
        quality: QualityService | None = None,
    ) -> AnalyzeResult:
        """Analyze trusted Gate output without rediscovering or rehashing it.

        Batch orchestration owns its first discovery pass. Reusing its immutable
        :class:`DiscoveredFile` records preserves that gate decision and avoids
        reopening every source merely to recompute an identical SHA-256.
        """
        reporter = weighted_progress(
            on_progress or noop_progress,
            stages=ANALYZE_STAGE_ORDER,
        )
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
                validate_extraction_result(
                    extracted,
                    source_id=f.source_id,
                    source_format=f.format,
                    content_sha256=f.content_sha256,
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
            except (
                ExtractionContractError,
                ValidationError,
                AttributeError,
                TypeError,
                ValueError,
            ) as exc:
                detail = (
                    str(exc)
                    if isinstance(exc, ExtractionContractError)
                    else f"{type(exc).__name__}"
                )
                errors.append(
                    GateError(
                        path=f.path,
                        code=ErrorCode.EXTRACT_RESULT_INVALID,
                        message=f"Extractor returned an invalid result: {detail}",
                        recovery=(
                            "Use an extractor that preserves source IDs, block "
                            "locators, hashes and deterministic block IDs."
                        ),
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
        if quality is not None:
            bundle = _apply_quality_review(bundle, quality)
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
        # Four adjacent Map chunks form one Reduce section. This remains
        # bounded even when an EPUB packages each printed page as a separate
        # spine item, while retaining enough local order for a method flow.
        reduce_group_by_chunk: dict[str, str] = {}
        for source_id in source_ids:
            source_chunks = [chunk for chunk in chunks if chunk.source_id == source_id]
            for index, chunk in enumerate(source_chunks):
                reduce_group_by_chunk[chunk.chunk_id] = (
                    f"{source_id}-section-{(index // 4) + 1}"
                )
        structure: list[StructureEntry] = []
        structure_by_source: dict[str, list[dict[str, object]]] = {}
        candidates: list[CandidateUnit] = []
        review_queue: list[ReviewItem] = []
        candidate_indices: dict[str, int] = {}
        chapter_candidates: dict[tuple[str, str], list[CandidateUnit]] = {}
        total_chunks = len(chunks)
        chunk_work = [
            max(
                1,
                sum(
                    estimate_tokens(item.text)
                    + estimate_tokens(item.context_before)
                    + estimate_tokens(item.context_after)
                    for item in chunk.items
                ),
            )
            for chunk in chunks
        ]
        total_work = sum(chunk_work)
        # The capability is only provided by built-in LLM adapters. Keeping the
        # legacy two-method protocol below preserves narrow test/future adapters.
        chunk_call = analyze_chunk
        runtime_llm = self._runtime_llm
        active_elapsed: dict[int, float] = {}

        def estimate_remaining(
            completed_indices: set[int],
        ) -> tuple[float | None, float | None, float | None, int | None]:
            if runtime_llm is None:
                return None, None, None, None
            indexed_estimates = [
                (
                    index,
                    runtime_llm.estimate_chunk_eta(list(chunks[index].items)),
                )
                for index in range(total_chunks)
                if index not in completed_indices
            ]
            if not indexed_estimates or any(
                estimate is None for _, estimate in indexed_estimates
            ):
                return None, None, None, None
            available = [
                (index, estimate)
                for index, estimate in indexed_estimates
                if estimate is not None
            ]
            point = [
                _remaining_duration(estimate.seconds, active_elapsed.get(index, 0.0))
                for index, estimate in available
            ]
            lower = [
                _remaining_duration(
                    estimate.lower_seconds, active_elapsed.get(index, 0.0)
                )
                for index, estimate in available
            ]
            upper = [
                _remaining_duration(
                    estimate.upper_seconds, active_elapsed.get(index, 0.0)
                )
                for index, estimate in available
            ]
            return (
                _parallel_makespan(point, runtime_llm.config.max_concurrent_requests),
                _parallel_makespan(lower, runtime_llm.config.max_concurrent_requests),
                _parallel_makespan(upper, runtime_llm.config.max_concurrent_requests),
                min(estimate.sample_count for _, estimate in available),
            )

        # Mark the structure stage as started *before* the (potentially slow)
        # bulk LLM call so the progress bar leaves the extraction stage's 100%
        # and shows "0 of N" instead of freezing on the previous stage.
        if total_chunks:
            eta, eta_lower, eta_upper, eta_samples = estimate_remaining(set())
            emit_progress(
                on_progress,
                ProgressEvent(
                    stage=STAGE_STRUCTURE,
                    current=0,
                    total=total_chunks,
                    status="started",
                    mode=(
                        "indeterminate"
                        if eta_samples in {None, 0}
                        else "estimated"
                    ),
                    work_completed=0,
                    work_total=total_work,
                    eta_seconds=eta,
                    eta_lower_seconds=eta_lower,
                    eta_upper_seconds=eta_upper,
                    eta_sample_count=eta_samples,
                ),
            )
        if runtime_llm is not None:
            completed_chunks = 0
            completed_indices: set[int] = set()

            def progress_estimate() -> tuple[
                float, float | None, float | None, float | None, int | None
            ]:
                work_completed = float(
                    sum(chunk_work[index] for index in completed_indices)
                )
                for index, elapsed in active_elapsed.items():
                    if index in completed_indices:
                        continue
                    estimate = runtime_llm.estimate_chunk_eta(list(chunks[index].items))
                    if estimate is not None:
                        work_completed += chunk_work[index] * _estimated_fraction(
                            elapsed, estimate.seconds
                        )
                eta, eta_lower, eta_upper, eta_samples = estimate_remaining(
                    completed_indices
                )
                return work_completed, eta, eta_lower, eta_upper, eta_samples

            def report_completion(completed: int, total: int, index: int) -> None:
                nonlocal completed_chunks
                completed_chunks = completed
                completed_indices.add(index)
                active_elapsed.pop(index, None)
                work_completed, eta, eta_lower, eta_upper, eta_samples = (
                    progress_estimate()
                )
                emit_progress(
                    on_progress,
                    ProgressEvent(
                        stage=STAGE_STRUCTURE,
                        current=completed,
                        total=total,
                        detail=chunks[index].source_id,
                        status="completed",
                        mode="determinate",
                        work_completed=work_completed,
                        work_total=total_work,
                        eta_seconds=eta,
                        eta_lower_seconds=eta_lower,
                        eta_upper_seconds=eta_upper,
                        eta_sample_count=eta_samples,
                    ),
                )

            def report_status(event: ChunkProgressEvent) -> None:
                if not 0 <= event.index < total_chunks:
                    return
                if event.status == "running":
                    active_elapsed[event.index] = event.elapsed_seconds
                elif event.status in {
                    "cached",
                    "rate_limited",
                    "retrying",
                    "failed",
                    "fallback",
                }:
                    active_elapsed.pop(event.index, None)
                work_completed, eta, eta_lower, eta_upper, eta_samples = (
                    progress_estimate()
                )
                emit_progress(
                    on_progress,
                    ProgressEvent(
                        stage=STAGE_STRUCTURE,
                        current=completed_chunks,
                        total=total_chunks,
                        detail=chunks[event.index].source_id,
                        status=(
                            "completed"
                            if event.status == "fallback"
                            else event.status
                        ),
                        mode=(
                            "estimated"
                            if event.heartbeat or eta_samples not in {None, 0}
                            else "indeterminate"
                        ),
                        work_completed=work_completed,
                        work_total=total_work,
                        eta_seconds=eta,
                        eta_lower_seconds=eta_lower,
                        eta_upper_seconds=eta_upper,
                        eta_sample_count=eta_samples,
                    ),
                )

            responses = runtime_llm.analyze_chunks(
                [
                    (
                        chunk.source_id,
                        list(chunk.items),
                        extractor_ids[chunk.source_id],
                    )
                    for chunk in chunks
                ],
                on_completed=report_completion,
                on_status=report_status,
            )
        else:
            responses = []
            for index, chunk in enumerate(chunks, start=1):
                responses.append(chunk_call(chunk.source_id, list(chunk.items)))
                on_progress(STAGE_STRUCTURE, index, total_chunks, chunk.source_id)
        responses_by_chunk = zip(chunks, responses, strict=True)
        for index, (chunk, response) in enumerate(responses_by_chunk, start=1):
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
                    # Keep the raw heading list per source for the synthesis
                    # prompt's organizing anchors (cheap, deterministic signal).
                    structure_by_source.setdefault(chunk.source_id, []).append(
                        dict(trusted)
                    )
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
                chapter_candidates.setdefault(
                    (chunk.source_id, reduce_group_by_chunk[chunk.chunk_id]), []
                ).append(candidate)
                self._maybe_flag(candidate, review_queue)

        synthesized = self._synthesize_hierarchy(
            source_ids=source_ids,
            chapter_candidates=chapter_candidates,
            candidate_indices=candidate_indices,
            reporter=on_progress,
            review_queue=review_queue,
            structure_by_source=structure_by_source,
        )
        if synthesized:
            candidates = synthesized
            review_queue.clear()
            for candidate in candidates:
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

    def _synthesize_hierarchy(
        self,
        *,
        source_ids: list[str],
        chapter_candidates: dict[tuple[str, str], list[CandidateUnit]],
        candidate_indices: dict[str, int],
        reporter: ProgressReporter,
        review_queue: list[ReviewItem],
        structure_by_source: dict[str, list[dict[str, object]]] | None = None,
    ) -> list[CandidateUnit]:
        """Run bounded-section → book reduction over cards, preserving refs.

        The Map output is intentionally not passed through verbatim to the
        final Bundle when synthesis succeeds.  That prevents hundreds of
        overlapping fragments from crowding out the executable whole-book
        workflow.  A failed reduction retains its prior level's cards.
        """
        synthesizer = getattr(self._llm, "synthesize_candidates", None)
        if not callable(synthesizer) or not chapter_candidates:
            return []

        # A spine item is not reliably a human chapter: many EPUBs put every
        # page or illustration in its own XHTML document.  Reduce the same
        # bounded Map sections instead, so the number of cloud reductions is
        # proportional to map chunks rather than arbitrary EPUB packaging.
        chapter_requests: list[
            tuple[str, str, list[dict[str, object]], list[CandidateUnit]]
        ] = []
        for (source_id, chapter), units in chapter_candidates.items():
            payload = self._bounded_evidence_payload(units, limit=24)
            if payload:
                chapter_requests.append((source_id, chapter, payload, units))
        if not chapter_requests:
            return []

        total_steps = len(chapter_requests) + len(source_ids)
        emit_progress(
            reporter,
            ProgressEvent(
                stage=STAGE_SYNTHESIS,
                current=0,
                total=total_steps,
                status="started",
                mode="indeterminate",
            ),
        )
        chapter_raw: dict[tuple[str, str], list[dict[str, object]] | None] = {}

        def synthesize_chapter(
            source_id: str,
            chapter: str,
            payload: list[dict[str, object]],
            units: list[CandidateUnit],
        ) -> tuple[tuple[str, str], list[dict[str, object]]]:
            del units
            anchors = (
                structure_by_source.get(source_id)
                if structure_by_source is not None
                else None
            )
            raw = synthesizer(source_id, "chapter", payload, anchors)
            if not isinstance(raw, list):
                return (source_id, chapter), []
            return (source_id, chapter), [
                dict(item) for item in raw if isinstance(item, dict)
            ]

        completed = 0
        runtime = self._runtime_llm
        if runtime is None:
            for request in chapter_requests:
                key, raw = synthesize_chapter(*request)
                chapter_raw[key] = raw
                completed += 1
                emit_progress(
                    reporter,
                    ProgressEvent(
                        stage=STAGE_SYNTHESIS,
                        current=completed,
                        total=total_steps,
                        detail=f"{key[0]}:{key[1]}",
                        status="completed",
                        mode="determinate",
                    ),
                )
        else:
            # Keep the cloud-facing reduction within the same conservative
            # provider limit used by Map; RuntimeLLMAdapter additionally
            # serializes request starts through its global RPM reservation.
            with ThreadPoolExecutor(
                max_workers=min(
                    runtime.config.max_concurrent_requests, len(chapter_requests)
                )
            ) as pool:
                futures = {
                    pool.submit(synthesize_chapter, *request): request
                    for request in chapter_requests
                }
                for future in as_completed(futures):
                    request = futures[future]
                    try:
                        key, raw = future.result()
                    except Exception:
                        # A completed Map cache remains a valid recovery
                        # point. Do not discard the entire book because one
                        # chapter Reduce call is temporarily unavailable.
                        key = (request[0], request[1])
                        raw = None
                    chapter_raw[key] = raw
                    completed += 1
                    emit_progress(
                        reporter,
                        ProgressEvent(
                            stage=STAGE_SYNTHESIS,
                            current=completed,
                            total=total_steps,
                            detail=f"{key[0]}:{key[1]}",
                            status="completed",
                            mode="determinate",
                        ),
                    )

        # Materialize Core-owned unit IDs in request order, never completion
        # order.  This keeps output stable even when cloud calls finish out of
        # order under bounded concurrency.
        chapter_results: dict[tuple[str, str], list[CandidateUnit]] = {}
        for source_id, chapter, _payload, original_units in chapter_requests:
            raw = chapter_raw.get((source_id, chapter))
            chapter_results[(source_id, chapter)] = (
                self._trusted_synthesized_candidates(
                    raw, original_units, candidate_indices
                )
                if raw is not None
                else original_units
            )

        final: list[CandidateUnit] = []
        for source_id in source_ids:
            source_chapters = [
                units
                for (candidate_source_id, _chapter), units in chapter_results.items()
                if candidate_source_id == source_id
            ]
            chapter_units = [unit for units in source_chapters for unit in units]
            payload = self._bounded_evidence_payload(chapter_units, limit=96)
            if not payload:
                continue
            anchors = (
                structure_by_source.get(source_id)
                if structure_by_source is not None
                else None
            )
            try:
                raw = synthesizer(source_id, "book", payload, anchors)
                book_units = self._trusted_synthesized_candidates(
                    raw, chapter_units, candidate_indices
                )
            except Exception:
                book_units = []
            final.extend(book_units or chapter_units)
            completed += 1
            emit_progress(
                reporter,
                ProgressEvent(
                    stage=STAGE_SYNTHESIS,
                    current=completed,
                    total=total_steps,
                    detail=source_id,
                    status="completed",
                    mode="determinate",
                ),
            )
        return final

    @staticmethod
    def _bounded_evidence_payload(
        units: list[CandidateUnit], *, limit: int
    ) -> list[dict[str, object]]:
        """Deduplicate locally and cap Reduce input before an LLM call."""
        priority = {
            "framework": 0,
            "principle": 1,
            "decision_rule": 2,
            "checklist": 3,
            "technique": 4,
            "anti_pattern": 5,
            "case": 6,
            "term": 7,
        }
        seen: set[str] = set()
        ordered = sorted(
            enumerate(units), key=lambda pair: (priority.get(pair[1].kind, 99), pair[0])
        )
        payload: list[dict[str, object]] = []
        for _index, unit in ordered:
            digest = hashlib.sha256(unit.content.strip().encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            payload.append(unit.model_dump(mode="json"))
            if len(payload) >= limit:
                break
        return payload

    @staticmethod
    def _trusted_synthesized_candidates(
        raw_entries: object,
        source_units: list[CandidateUnit],
        candidate_indices: dict[str, int],
    ) -> list[CandidateUnit]:
        """Rebuild model reductions with Core-owned IDs and source pointers."""
        if not isinstance(raw_entries, list):
            return []
        by_unit_id = {unit.unit_id: unit for unit in source_units}
        result: list[CandidateUnit] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            requested_ids = raw.get("source_unit_ids")
            if not isinstance(requested_ids, list):
                continue
            supporting = [
                by_unit_id[unit_id]
                for unit_id in requested_ids
                if isinstance(unit_id, str) and unit_id in by_unit_id
            ]
            if not supporting:
                continue
            source_refs: list[dict[str, object]] = []
            seen_refs: set[tuple[str, str]] = set()
            for unit in supporting:
                for ref in unit.source_refs:
                    source_id = ref.get("source_id")
                    block_id = ref.get("block_id")
                    if not isinstance(source_id, str) or not isinstance(block_id, str):
                        continue
                    key = (source_id, block_id)
                    if key not in seen_refs:
                        seen_refs.add(key)
                        source_refs.append(
                            {"source_id": source_id, "block_id": block_id}
                        )
            if not source_refs:
                continue
            first_block = str(source_refs[0]["block_id"])
            candidate_indices[first_block] = candidate_indices.get(first_block, 0) + 1
            candidate_data = {
                "unit_id": derive_candidate_unit_id(
                    first_block, candidate_indices[first_block]
                ),
                "kind": raw.get("kind", "technique"),
                "content": raw.get("content", ""),
                "conditions": raw.get("conditions", []),
                "exceptions": raw.get("exceptions", []),
                "source_refs": source_refs,
                "confidence": raw.get("confidence", 0.5),
                "review_status": "candidate",
                "record_version": 1,
            }
            try:
                result.append(CandidateUnit.model_validate(candidate_data))
            except ValidationError:
                continue
        return result

    def _suggest_skills(
        self,
        source_ids: list[str],
        candidates: list[CandidateUnit],
        reporter: ProgressReporter,
    ) -> list[SuggestedSkill]:
        suggested: list[SuggestedSkill] = []
        requests: list[tuple[str, list[dict[str, object]], int]] = []
        for source_id in source_ids:
            source_cands = [
                candidate.model_dump(mode="json")
                for candidate in candidates
                if any(
                    ref.get("source_id") == source_id
                    for ref in candidate.source_refs
                )
            ]
            if source_cands:
                input_tokens = max(
                    1,
                    estimate_tokens(
                        json.dumps(
                            source_cands,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    ),
                )
                requests.append((source_id, source_cands, input_tokens))
        total_sources = len(requests)
        total_work = sum(tokens for _, _, tokens in requests)
        runtime_llm = self._runtime_llm

        def estimate_from(start_index: int, elapsed: float = 0.0) -> tuple[
            float | None, float | None, float | None, int | None
        ]:
            if runtime_llm is None:
                return None, None, None, None
            estimates = [
                runtime_llm.estimate_input_eta(tokens, operation="skills")
                for _, _, tokens in requests[start_index:]
            ]
            if not estimates or any(estimate is None for estimate in estimates):
                return None, None, None, None
            available = [estimate for estimate in estimates if estimate is not None]
            first = available[0]
            return (
                _remaining_duration(first.seconds, elapsed)
                + sum(estimate.seconds for estimate in available[1:]),
                _remaining_duration(first.lower_seconds, elapsed)
                + sum(estimate.lower_seconds for estimate in available[1:]),
                _remaining_duration(first.upper_seconds, elapsed)
                + sum(estimate.upper_seconds for estimate in available[1:]),
                min(estimate.sample_count for estimate in available),
            )

        # Announce the skills stage before the first (slow) LLM call so the
        # bar does not sit on the candidates stage's 100% while waiting.
        if total_sources:
            eta, eta_lower, eta_upper, eta_samples = estimate_from(0)
            emit_progress(
                reporter,
                ProgressEvent(
                    stage=STAGE_SKILLS,
                    current=0,
                    total=total_sources,
                    status="started",
                    mode=(
                        "indeterminate"
                        if eta_samples in {None, 0}
                        else "estimated"
                    ),
                    work_completed=0,
                    work_total=total_work,
                    eta_seconds=eta,
                    eta_lower_seconds=eta_lower,
                    eta_upper_seconds=eta_upper,
                    eta_sample_count=eta_samples,
                ),
            )
        completed_work = 0.0
        for request_index, (source_id, source_cands, input_tokens) in enumerate(
            requests
        ):
            def report_status(
                event: ChunkProgressEvent,
                *,
                current_index: int = request_index,
                current_source_id: str = source_id,
                current_input_tokens: int = input_tokens,
                base_completed: float = completed_work,
            ) -> None:
                elapsed = event.elapsed_seconds if event.status == "running" else 0.0
                current_estimate = (
                    runtime_llm.estimate_input_eta(
                        current_input_tokens, operation="skills"
                    )
                    if runtime_llm is not None
                    else None
                )
                projected = base_completed
                if current_estimate is not None:
                    projected += current_input_tokens * _estimated_fraction(
                        elapsed, current_estimate.seconds
                    )
                eta, eta_lower, eta_upper, eta_samples = estimate_from(
                    current_index, elapsed
                )
                emit_progress(
                    reporter,
                    ProgressEvent(
                        stage=STAGE_SKILLS,
                        current=current_index,
                        total=total_sources,
                        detail=current_source_id,
                        status=(
                            "completed"
                            if event.status == "fallback"
                            else event.status
                        ),
                        mode=(
                            "estimated"
                            if event.heartbeat or eta_samples not in {None, 0}
                            else "indeterminate"
                        ),
                        work_completed=projected,
                        work_total=total_work,
                        eta_seconds=eta,
                        eta_lower_seconds=eta_lower,
                        eta_upper_seconds=eta_upper,
                        eta_sample_count=eta_samples,
                    ),
                )

            raw_suggestions = (
                runtime_llm.suggest_skills_with_progress(
                    source_id,
                    source_cands,
                    on_status=report_status,
                )
                if runtime_llm is not None
                else self._llm.suggest_skills(source_id, source_cands)
            )
            for suggestion in raw_suggestions:
                try:
                    suggested.append(SuggestedSkill.model_validate(suggestion))
                except ValidationError:
                    continue
            # Report completion *after* the LLM call for this source.
            completed_work += input_tokens
            eta, eta_lower, eta_upper, eta_samples = estimate_from(request_index + 1)
            emit_progress(
                reporter,
                ProgressEvent(
                    stage=STAGE_SKILLS,
                    current=request_index + 1,
                    total=total_sources,
                    detail=source_id,
                    status="completed",
                    mode="determinate",
                    work_completed=completed_work,
                    work_total=total_work,
                    eta_seconds=eta,
                    eta_lower_seconds=eta_lower,
                    eta_upper_seconds=eta_upper,
                    eta_sample_count=eta_samples,
                ),
            )
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
