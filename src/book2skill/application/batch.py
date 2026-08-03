"""Batch orchestration use case (TASK-010, PRD FR-02 batch partial success).

Processes many input files with per-file failure isolation: a single corrupt
or unsupported file never blocks the rest. Each input yields its own
:class:`~book2skill.application.models.FileOutcome` (with its own bundle when
extraction succeeds), and the aggregated :class:`BatchResult` carries a
summary and a flattened failure list.

This is a thin orchestrator over
:class:`~book2skill.application.analyze.AnalyzeUseCase`: it relies on the
existing gate (discovery/validation/hash/dedup) and the existing per-file
extraction isolation (``DomainError`` → ``GateError``).

When *max_workers* > 1 the per-file analysis runs on a
:class:`~concurrent.futures.ThreadPoolExecutor`. Outcomes are always
returned in input order; the progress callback fires in completion order
(not guaranteed to match input order under concurrency). Concurrent mode
should be paired with a real ``data_home`` so the file-based
:class:`~book2skill.storage.FileRawStorage` (atomic writes) is used instead
of the in-memory storage, which is not thread-safe.

Checkpoint / resume (PRD P1): when *checkpoint_path* is provided the
orchestrator writes a JSON checkpoint after each file. If the run is
interrupted, re-invoking with *resume* = ``True`` skips files that already
succeeded or partially succeeded; failed/skipped files are retried. The
checkpoint is deleted on full completion.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from book2skill.application.analyze import AnalyzeUseCase
from book2skill.application.gate import DiscoveredFile, Gate
from book2skill.application.models import (
    BatchResult,
    BatchSummary,
    FailureRecord,
    FileOutcome,
    FileStatus,
)
from book2skill.llm.runtime import LLMRuntimeConfig
from book2skill.storage.file_storage import atomic_write

#: Progress callback signature: ``(index, total, path)`` where *index* is the
#: 1-based position of the file currently being processed.
ProgressCallback = Callable[[int, int, str], None]

#: Statuses considered "done" for checkpoint purposes — not retried on resume.
#: Failed/skipped files are retried because their outcome may differ on a
#: subsequent run (e.g. transient I/O error, newly installed extractor).
_CHECKPOINTABLE: frozenset[str] = frozenset({"success", "partial"})


class CheckpointManager:
    """Read/write/clear the batch checkpoint file.

    The checkpoint is a plain JSON file recording the resolved path and
    outcome status of every file that produced a bundle (``success`` or
    ``partial``). It is written atomically after each file and deleted on
    full completion.
    """

    def __init__(self, checkpoint_path: Path) -> None:
        self._path = checkpoint_path

    def load_completed(self) -> dict[str, str]:
        """Return the ``{resolved_path: status}`` map, or ``{}`` if absent."""
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        completed = data.get("completed", {})
        if not isinstance(completed, dict):
            return {}
        return {str(k): str(v) for k, v in completed.items()}

    def save(self, completed: dict[str, str]) -> None:
        """Atomically write the checkpoint."""
        data = {
            "schema_version": 1,
            "completed": completed,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self._path, json.dumps(data, indent=2, ensure_ascii=False))

    def clear(self) -> None:
        """Delete the checkpoint (called on full completion)."""
        if self._path.exists():
            self._path.unlink()


class BatchOrchestrator:
    """Orchestrate per-file analysis across a batch of inputs.

    Dependencies mirror :class:`~book2skill.application.analyze.AnalyzeUseCase`
    so tests can inject fakes. By default a fresh :class:`Gate` and
    :class:`AnalyzeUseCase` are constructed sharing the same *data_home*.
    """

    def __init__(
        self,
        *,
        gate: Gate | None = None,
        use_case: AnalyzeUseCase | None = None,
        data_home: Path | None = None,
        max_workers: int = 1,
        runtime_config: LLMRuntimeConfig | None = None,
    ) -> None:
        self._gate = gate or Gate()
        self._use_case = use_case or AnalyzeUseCase(
            data_home=data_home, runtime_config=runtime_config
        )
        self._max_workers = max(1, max_workers)

    def execute(
        self,
        inputs: list[str],
        *,
        rights_note: str | None = None,
        on_progress: ProgressCallback | None = None,
        checkpoint_path: Path | None = None,
        resume: bool = False,
    ) -> BatchResult:
        """Run per-file analysis on *inputs*.

        Args:
            inputs: File paths, directories or glob patterns (expanded by the
                gate into individual files).
            rights_note: Optional rights-confirmation note forwarded to every
                per-file analysis.
            on_progress: Optional callback invoked before each discovered file
                is processed; never invoked for gate-rejected inputs. Under
                concurrency (*max_workers* > 1) the callback fires in
                completion order, not input order.
            checkpoint_path: Optional path for a JSON checkpoint file. When
                set, the orchestrator records each completed file so an
                interrupted run can be resumed.
            resume: When ``True`` and *checkpoint_path* exists, skip files
                that already succeeded or partially succeeded in a prior run.
                Failed/skipped files are retried.

        Returns:
            A :class:`BatchResult` with one :class:`FileOutcome` per discovered
            or rejected input, ordered to match the gate's discovery order.
        """
        files, gate_errors = self._gate.discover(inputs)

        outcomes: list[FileOutcome] = []

        # Gate-rejected inputs (missing/empty/oversized/damaged) never enter
        # extraction; they are recorded as skipped so callers can distinguish
        # "input unusable" from "extraction failed".
        for err in gate_errors:
            outcomes.append(
                FileOutcome(
                    path=str(err.path),
                    status="skipped",
                    errors=[FailureRecord.from_gate_error(err)],
                )
            )

        # Load checkpoint for resume.
        ckpt: CheckpointManager | None = None
        completed: dict[str, str] = {}
        if checkpoint_path is not None:
            ckpt = CheckpointManager(checkpoint_path)
            if resume:
                completed = ckpt.load_completed()
            else:
                # Fresh run: start with an empty checkpoint.
                ckpt.save({})

        # Partition files into skipped (already done) and pending.
        pending: list[DiscoveredFile] = []
        for discovered in files:
            resolved = str(discovered.path.resolve())
            if resolved in completed and completed[resolved] in _CHECKPOINTABLE:
                # Reconstruct a minimal outcome from the checkpoint.
                outcomes.append(
                    FileOutcome(
                        path=str(discovered.path),
                        source_id=discovered.source_id,
                        status=completed[resolved],  # type: ignore[arg-type]
                        format=discovered.format.value,
                        bundle=None,
                        errors=[],
                    )
                )
            else:
                pending.append(discovered)

        total = len(pending)
        if self._max_workers > 1 and total > 1:
            file_outcomes = self._run_concurrent(
                pending, total, rights_note, on_progress, ckpt, completed
            )
        else:
            file_outcomes = self._run_sequential(
                pending, total, rights_note, on_progress, ckpt, completed
            )
        outcomes.extend(file_outcomes)

        # Full completion → clear the checkpoint.
        if ckpt is not None:
            ckpt.clear()

        summary = _tally(outcomes)
        failure_list = [
            err
            for o in outcomes
            if o.status in ("failed", "skipped")
            for err in o.errors
        ]
        return BatchResult(
            outcomes=outcomes,
            summary=summary,
            failure_list=failure_list,
        )

    def _run_sequential(
        self,
        files: list[DiscoveredFile],
        total: int,
        rights_note: str | None,
        on_progress: ProgressCallback | None,
        ckpt: CheckpointManager | None = None,
        completed: dict[str, str] | None = None,
    ) -> list[FileOutcome]:
        """Process files one at a time, preserving progress-callback order."""
        outcomes: list[FileOutcome] = []
        for index, discovered in enumerate(files, start=1):
            if on_progress is not None:
                on_progress(index, total, str(discovered.path))
            outcome = self._process_file(discovered, rights_note)
            outcomes.append(outcome)
            if ckpt is not None and completed is not None:
                if outcome.status in _CHECKPOINTABLE:
                    completed[str(discovered.path.resolve())] = outcome.status
                ckpt.save(completed)
        return outcomes

    def _run_concurrent(
        self,
        files: list[DiscoveredFile],
        total: int,
        rights_note: str | None,
        on_progress: ProgressCallback | None,
        ckpt: CheckpointManager | None = None,
        completed: dict[str, str] | None = None,
    ) -> list[FileOutcome]:
        """Process files in parallel, returning outcomes in input order."""
        results: dict[int, FileOutcome] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            future_to_index: dict[Future[FileOutcome], int] = {
                pool.submit(self._process_file, discovered, rights_note): idx
                for idx, discovered in enumerate(files, start=1)
            }
            for done_count, future in enumerate(
                as_completed(future_to_index), start=1
            ):
                idx = future_to_index[future]
                results[idx] = future.result()
                if on_progress is not None:
                    on_progress(done_count, total, "")
        # Persist checkpoint after all futures complete (concurrent writes
        # to the same file would race; we flush once at the end).
        if ckpt is not None and completed is not None:
            for i in range(1, len(files) + 1):
                outcome = results.get(i)
                if outcome is not None and outcome.status in _CHECKPOINTABLE:
                    completed[
                        str(files[i - 1].path.resolve())
                    ] = outcome.status
            ckpt.save(completed)
        return [results[i] for i in range(1, len(files) + 1)]

    def _process_file(
        self, discovered: DiscoveredFile, rights_note: str | None
    ) -> FileOutcome:
        """Analyse a single discovered file and build its :class:`FileOutcome`."""
        result = self._use_case.execute_discovered(
            [discovered],
            rights_note=rights_note,
        )

        bundle = result.bundle
        if bundle is not None and not result.errors:
            status: FileStatus = "success"
        elif bundle is not None:
            status = "partial"
        else:
            status = "failed"

        source_id = (
            bundle.source_ids[0] if bundle is not None else discovered.source_id
        )
        return FileOutcome(
            path=str(discovered.path),
            source_id=source_id,
            status=status,
            format=discovered.format.value,
            bundle=bundle,
            errors=[FailureRecord.from_gate_error(e) for e in result.errors],
        )


def _tally(outcomes: list[FileOutcome]) -> BatchSummary:
    """Count outcomes by status into a :class:`BatchSummary`."""
    counts = {"success": 0, "partial": 0, "failed": 0, "skipped": 0}
    for o in outcomes:
        counts[o.status] += 1
    return BatchSummary(
        total=len(outcomes),
        succeeded=counts["success"],
        partial=counts["partial"],
        failed=counts["failed"],
        skipped=counts["skipped"],
    )


__all__ = [
    "BatchOrchestrator",
    "BatchResult",
    "CheckpointManager",
    "ProgressCallback",
]
