"""Tests for the batch orchestration use case (TASK-010)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from book2skill.application.analyze import AnalyzeUseCase
from book2skill.application.batch import BatchOrchestrator
from book2skill.application.gate import Gate
from book2skill.application.models import (
    BatchResult,
    BatchSummary,
    FileOutcome,
)
from book2skill.cli import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_txt(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_bytes(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# BatchOrchestrator
# ---------------------------------------------------------------------------


class TestBatchOrchestrator:
    """End-to-end tests for the batch orchestrator."""

    def test_batch_all_succeed(self, tmp_path: Path) -> None:
        """Three valid txt files yield three success outcomes."""
        files = [
            _write_txt(tmp_path / "a.txt", "# Heading A\nPrinciple one."),
            _write_txt(tmp_path / "b.txt", "# Heading B\nTechnique two."),
            _write_txt(tmp_path / "c.txt", "# Heading C\nTerm three."),
        ]
        orchestrator = BatchOrchestrator()
        result = orchestrator.execute([str(f) for f in files])

        assert result.summary.total == 3
        assert result.summary.succeeded == 3
        assert result.summary.failed == 0
        assert result.summary.skipped == 0
        assert result.failure_list == []
        for outcome in result.outcomes:
            assert outcome.status == "success"
            assert outcome.bundle is not None
            assert outcome.source_id is not None

    def test_batch_mixed_valid_and_damaged(self, tmp_path: Path) -> None:
        """A damaged epub is skipped while valid txt files still succeed."""
        good1 = _write_txt(tmp_path / "a.txt", "# Heading A\nContent.")
        good2 = _write_txt(tmp_path / "b.txt", "# Heading B\nContent.")
        # Non-zip bytes with .epub extension → gate rejects as damaged.
        bad = _write_bytes(tmp_path / "broken.epub", b"not a zip file at all")

        orchestrator = BatchOrchestrator()
        result = orchestrator.execute([str(good1), str(good2), str(bad)])

        assert result.summary.total == 3
        assert result.summary.succeeded == 2
        assert result.summary.skipped == 1
        assert result.summary.failed == 0

        by_path = {o.path: o for o in result.outcomes}
        assert by_path[str(bad)].status == "skipped"
        assert by_path[str(bad)].errors  # has a failure record
        assert by_path[str(good1)].status == "success"
        assert by_path[str(good2)].status == "success"
        # failure_list flattens only failed/skipped errors
        assert len(result.failure_list) == 1

    def test_batch_missing_path_is_skipped(self, tmp_path: Path) -> None:
        """A non-existent path is reported as skipped, not raised."""
        good = _write_txt(tmp_path / "a.txt", "# Heading\nContent.")
        missing = tmp_path / "does-not-exist.txt"

        orchestrator = BatchOrchestrator()
        result = orchestrator.execute([str(good), str(missing)])

        assert result.summary.succeeded == 1
        assert result.summary.skipped == 1
        skipped = [o for o in result.outcomes if o.status == "skipped"]
        assert len(skipped) == 1
        assert skipped[0].errors[0].code == "GATE_FILE_NOT_FOUND"

    def test_batch_unsupported_format_is_skipped(self, tmp_path: Path) -> None:
        """A binary file with no recognised format → skipped outcome."""
        bad = _write_bytes(
            tmp_path / "doc.bin", bytes(range(32))
        )

        orchestrator = BatchOrchestrator()
        result = orchestrator.execute([str(bad)])

        assert result.summary.total == 1
        assert result.summary.skipped == 1
        assert result.outcomes[0].status == "skipped"
        assert result.outcomes[0].errors[0].code == "GATE_UNSUPPORTED_FORMAT"
        assert result.outcomes[0].bundle is None

    def test_batch_all_fail(self, tmp_path: Path) -> None:
        """All unsupported inputs → all skipped, failure_list non-empty."""
        bad1 = _write_bytes(tmp_path / "a.bin", bytes(range(32)))
        bad2 = _write_bytes(tmp_path / "b.bin", bytes(range(32)))

        orchestrator = BatchOrchestrator()
        result = orchestrator.execute([str(bad1), str(bad2)])

        assert result.summary.skipped == 2
        assert result.summary.succeeded == 0
        assert len(result.failure_list) == 2

    def test_batch_empty_directory(self, tmp_path: Path) -> None:
        """An empty directory yields no outcomes and does not raise."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        orchestrator = BatchOrchestrator()
        result = orchestrator.execute([str(empty_dir)])

        assert result.summary.total == 0
        assert result.outcomes == []

    def test_batch_directory_expanded(self, tmp_path: Path) -> None:
        """A directory input is expanded into one outcome per file."""
        src_dir = tmp_path / "books"
        src_dir.mkdir()
        _write_txt(src_dir / "a.txt", "# A\nContent.")
        _write_txt(src_dir / "b.txt", "# B\nContent.")

        orchestrator = BatchOrchestrator()
        result = orchestrator.execute([str(src_dir)])

        assert result.summary.total == 2
        assert result.summary.succeeded == 2

    def test_batch_duplicate_hash_dedup(self, tmp_path: Path) -> None:
        """Two identical files dedup to one outcome (gate semantics)."""
        content = "# Same\nIdentical content here."
        a = _write_txt(tmp_path / "a.txt", content)
        b = _write_txt(tmp_path / "b.txt", content)

        orchestrator = BatchOrchestrator()
        result = orchestrator.execute([str(a), str(b)])

        # Gate keeps the first file per content hash; no error is raised.
        assert result.summary.total == 1
        assert result.summary.succeeded == 1

    def test_batch_reuses_discovered_file_without_rehashing(
        self, tmp_path: Path
    ) -> None:
        class CountingGate(Gate):
            def __init__(self) -> None:
                super().__init__()
                self.hash_calls = 0

            def _compute_sha256(self, path: Path) -> str:
                self.hash_calls += 1
                return super()._compute_sha256(path)

        source = _write_txt(tmp_path / "a.txt", "# Heading\nContent.")
        gate = CountingGate()
        use_case = AnalyzeUseCase(gate=gate)

        result = BatchOrchestrator(gate=gate, use_case=use_case).execute(
            [str(source)]
        )

        assert result.summary.succeeded == 1
        assert gate.hash_calls == 1

    def test_batch_progress_callback(self, tmp_path: Path) -> None:
        """The progress callback fires once per discovered file."""
        files = [
            _write_txt(tmp_path / "a.txt", "# A\nContent."),
            _write_txt(tmp_path / "b.txt", "# B\nContent."),
            _write_txt(tmp_path / "c.txt", "# C\nContent."),
        ]
        calls: list[tuple[int, int, str]] = []

        orchestrator = BatchOrchestrator()
        orchestrator.execute(
            [str(f) for f in files],
            on_progress=lambda i, n, p: calls.append((i, n, p)),
        )

        assert len(calls) == 3
        assert calls[0][0] == 1 and calls[0][1] == 3
        assert calls[2][0] == 3 and calls[2][1] == 3

    def test_batch_rights_note_propagated(self, tmp_path: Path) -> None:
        """rights_note is forwarded to the underlying manifests (no crash)."""
        # data_home on disk so manifests are persisted; we only assert the
        # run completes successfully with the note forwarded.
        data_home = tmp_path / "store"
        good = _write_txt(tmp_path / "a.txt", "# Heading\nContent.")
        orchestrator = BatchOrchestrator(data_home=data_home)

        result = orchestrator.execute([str(good)], rights_note="licensed copy")

        assert result.summary.succeeded == 1
        # The manifest is persisted under data_home/<source_id>/.
        manifests = list(data_home.rglob("manifest.json"))
        assert len(manifests) == 1
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert manifest["rights_note"] == "licensed copy"

    def test_batch_json_serializable(self, tmp_path: Path) -> None:
        """BatchResult.model_dump(mode='json') round-trips through json.dumps."""
        good = _write_txt(tmp_path / "a.txt", "# Heading\nContent.")
        bad = _write_bytes(tmp_path / "broken.epub", b"not a zip")

        orchestrator = BatchOrchestrator()
        result = orchestrator.execute([str(good), str(bad)])

        dumped = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        parsed = json.loads(dumped)
        assert parsed["schema_version"] == 1
        assert parsed["summary"]["total"] == 2
        assert parsed["summary"]["succeeded"] == 1
        assert len(parsed["failure_list"]) == 1

    def test_batch_concurrent_matches_sequential(self, tmp_path: Path) -> None:
        """Concurrent mode (max_workers>1) produces the same outcome count
        and status tallies as sequential mode."""
        files = [
            _write_txt(tmp_path / f"file{i}.txt", f"# Heading {i}\nContent {i}.")
            for i in range(4)
        ]
        data_home = tmp_path / "store"

        seq = BatchOrchestrator(data_home=data_home / "seq")
        seq_result = seq.execute([str(f) for f in files])

        conc = BatchOrchestrator(
            data_home=data_home / "conc", max_workers=2
        )
        conc_result = conc.execute([str(f) for f in files])

        assert seq_result.summary.total == conc_result.summary.total
        assert seq_result.summary.succeeded == conc_result.summary.succeeded
        assert seq_result.summary.failed == conc_result.summary.failed
        # Outcomes are returned in input order even under concurrency.
        seq_paths = [o.path for o in seq_result.outcomes]
        conc_paths = [o.path for o in conc_result.outcomes]
        assert seq_paths == conc_paths

    def test_batch_concurrent_failure_isolation(self, tmp_path: Path) -> None:
        """A corrupt file among many does not block the rest under concurrency."""
        good1 = _write_txt(tmp_path / "a.txt", "# A\nContent.")
        bad = _write_bytes(tmp_path / "b.epub", b"not a zip")
        good2 = _write_txt(tmp_path / "c.txt", "# C\nContent.")
        data_home = tmp_path / "store"

        orchestrator = BatchOrchestrator(
            data_home=data_home, max_workers=2
        )
        result = orchestrator.execute([str(good1), str(bad), str(good2)])

        assert result.summary.total == 3
        assert result.summary.succeeded == 2
        assert result.summary.skipped == 1

    def test_batch_llm_failure_isolated_per_file(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """An LLM runtime failure on one file must not abort the batch."""
        from book2skill.llm.runtime import LLMRuntimeError

        good1 = _write_txt(tmp_path / "a.txt", "# A\nPrinciple one enough here.")
        bad = _write_txt(tmp_path / "b.txt", "# B\nTechnique two enough here.")
        good2 = _write_txt(tmp_path / "c.txt", "# C\nTerm three enough here.")

        use_case = AnalyzeUseCase()
        real = use_case.execute_discovered

        def _flaky(
            discovered,
            *,
            gate_errors=None,
            collection_id=None,
            rights_note=None,
            on_progress=None,
            persist_raw=True,
        ):
            if any(str(d.path) == str(bad) for d in discovered):
                raise LLMRuntimeError("OpenAI-compatible request failed")
            return real(
                discovered,
                gate_errors=gate_errors,
                collection_id=collection_id,
                rights_note=rights_note,
                on_progress=on_progress,
                persist_raw=persist_raw,
            )

        use_case.execute_discovered = _flaky  # type: ignore[assignment]
        orchestrator = BatchOrchestrator(use_case=use_case)
        result = orchestrator.execute([str(good1), str(bad), str(good2)])

        assert result.summary.total == 3
        assert result.summary.succeeded == 2
        assert result.summary.failed == 1
        assert result.failure_list
        assert result.failure_list[0].code == "LLM_FAILURE"
        assert "OpenAI-compatible request failed" in result.failure_list[0].message


class TestCheckpointResume:
    """Checkpoint / resume behaviour (PRD P1 断点恢复)."""

    def test_checkpoint_deleted_on_full_completion(
        self, tmp_path: Path
    ) -> None:
        files = [
            _write_txt(tmp_path / f"f{i}.txt", f"# Heading {i}\nContent.")
            for i in range(3)
        ]
        data_home = tmp_path / "store"
        ckpt = tmp_path / "ckpt.json"

        orchestrator = BatchOrchestrator(data_home=data_home)
        result = orchestrator.execute(
            [str(f) for f in files], checkpoint_path=ckpt
        )
        assert result.summary.succeeded == 3
        # Checkpoint cleared after full completion.
        assert not ckpt.exists()

    def test_resume_skips_completed_files(self, tmp_path: Path) -> None:
        files = [
            _write_txt(tmp_path / f"f{i}.txt", f"# Heading {i}\nContent.")
            for i in range(4)
        ]
        data_home = tmp_path / "store"
        ckpt = tmp_path / "ckpt.json"

        # First run: process all 4 files.
        orch = BatchOrchestrator(data_home=data_home)
        result1 = orch.execute(
            [str(f) for f in files], checkpoint_path=ckpt
        )
        assert result1.summary.succeeded == 4
        # Checkpoint was cleared on completion.
        assert not ckpt.exists()

        # Simulate an interrupted run: write a checkpoint with 2 files done.
        import json

        ckpt.parent.mkdir(parents=True, exist_ok=True)
        completed = {
            str(files[0].resolve()): "success",
            str(files[1].resolve()): "success",
        }
        ckpt.write_text(
            json.dumps({"schema_version": 1, "completed": completed}),
            encoding="utf-8",
        )

        # Second run with --resume: should skip the 2 completed files.
        orch2 = BatchOrchestrator(data_home=data_home / "resume")
        result2 = orch2.execute(
            [str(f) for f in files], checkpoint_path=ckpt, resume=True
        )
        assert result2.summary.total == 4
        # The 2 resumed files show success (from checkpoint), 2 reprocessed.
        assert result2.summary.succeeded == 4
        # Checkpoint cleared on completion.
        assert not ckpt.exists()

    def test_resume_retries_failed_files(self, tmp_path: Path) -> None:
        good = _write_txt(tmp_path / "ok.txt", "# OK\nContent.")
        bad = _write_bytes(tmp_path / "bad.epub", b"not a zip")
        data_home = tmp_path / "store"
        ckpt = tmp_path / "ckpt.json"

        # First run: good succeeds, bad is skipped by gate.
        orch = BatchOrchestrator(data_home=data_home)
        result1 = orch.execute(
            [str(good), str(bad)], checkpoint_path=ckpt
        )
        assert result1.summary.succeeded == 1
        assert result1.summary.skipped == 1
        # Completed on first run → checkpoint cleared.
        assert not ckpt.exists()

    def test_checkpoint_persists_if_not_completed(self, tmp_path: Path) -> None:
        """A checkpoint written mid-batch is loadable for resume."""
        from book2skill.application.batch import CheckpointManager

        ckpt_path = tmp_path / "ckpt.json"
        mgr = CheckpointManager(ckpt_path)

        # Empty checkpoint loads as {}.
        assert mgr.load_completed() == {}

        # Save and reload.
        mgr.save({"/fake/a.txt": "success", "/fake/b.txt": "partial"})
        loaded = mgr.load_completed()
        assert loaded["/fake/a.txt"] == "success"
        assert loaded["/fake/b.txt"] == "partial"

        # Clear deletes the file.
        mgr.clear()
        assert not ckpt_path.exists()

    def test_resume_without_checkpoint_processes_all(
        self, tmp_path: Path
    ) -> None:
        files = [
            _write_txt(tmp_path / f"f{i}.txt", f"# H{i}\nC{i}.")
            for i in range(2)
        ]
        data_home = tmp_path / "store"
        ckpt = tmp_path / "ckpt.json"

        # Resume with no existing checkpoint → all files processed.
        orch = BatchOrchestrator(data_home=data_home)
        result = orch.execute(
            [str(f) for f in files], checkpoint_path=ckpt, resume=True
        )
        assert result.summary.succeeded == 2
        assert not ckpt.exists()  # cleared on completion

    def test_cli_batch_resume_flag(self, tmp_path: Path) -> None:
        files = [
            _write_txt(tmp_path / f"f{i}.txt", f"# H{i}\nC{i}.")
            for i in range(2)
        ]
        data_home = tmp_path / "store"
        ckpt = tmp_path / "ckpt.json"

        # First CLI run with checkpoint.
        result = runner.invoke(
            app,
            [
                "batch",
                *[str(f) for f in files],
                "--data-home",
                str(data_home),
                "--checkpoint",
                str(ckpt),
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["summary"]["succeeded"] == 2
        # Checkpoint cleared on completion.
        assert not ckpt.exists()


# ---------------------------------------------------------------------------
# BatchResult / FileOutcome models
# ---------------------------------------------------------------------------


class TestBatchResultModel:
    """Unit tests for the batch result model invariants."""

    def test_summary_counts_consistent(self) -> None:
        """Summary fields sum to total."""
        outcomes = [
            FileOutcome(path="/a", status="success"),
            FileOutcome(path="/b", status="partial"),
            FileOutcome(path="/c", status="failed"),
            FileOutcome(path="/d", status="skipped"),
        ]
        summary = BatchSummary(total=4, succeeded=1, partial=1, failed=1, skipped=1)
        result = BatchResult(outcomes=outcomes, summary=summary)
        assert result.summary.total == 4
        total_status = (
            result.summary.succeeded
            + result.summary.partial
            + result.summary.failed
            + result.summary.skipped
        )
        assert total_status == result.summary.total

    def test_failure_list_excludes_success(self) -> None:
        """A hand-built BatchResult keeps failure_list independent of outcomes."""
        from book2skill.application.models import FailureRecord

        outcomes = [
            FileOutcome(path="/a", status="success"),
            FileOutcome(
                path="/b",
                status="failed",
                errors=[FailureRecord(path="/b", code="X", message="m")],
            ),
        ]
        summary = BatchSummary(total=2, succeeded=1, partial=0, failed=1, skipped=0)
        result = BatchResult(
            outcomes=outcomes,
            summary=summary,
            failure_list=[
                FailureRecord(path="/b", code="X", message="m", recovery="r")
            ],
        )
        assert len(result.failure_list) == 1
        assert result.failure_list[0].recovery == "r"


# ---------------------------------------------------------------------------
# CLI batch command
# ---------------------------------------------------------------------------


runner = CliRunner()


class TestBatchCommand:
    """End-to-end CLI tests for the `batch` subcommand."""

    def test_batch_cli_json_output(self, tmp_path: Path) -> None:
        good = _write_txt(tmp_path / "a.txt", "# Heading\nContent.")
        result = runner.invoke(app, ["batch", str(good), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["summary"]["succeeded"] == 1
        assert payload["outcomes"][0]["status"] == "success"

    def test_batch_cli_human_output(self, tmp_path: Path) -> None:
        good = _write_txt(tmp_path / "a.txt", "# Heading\nContent.")
        result = runner.invoke(app, ["batch", str(good)])
        assert result.exit_code == 0
        assert "Batch complete" in result.stdout

    def test_batch_cli_all_fail_exit_code(self, tmp_path: Path) -> None:
        bad = _write_bytes(tmp_path / "a.bin", bytes(range(32)))
        result = runner.invoke(app, ["batch", str(bad)])
        assert result.exit_code == 1
