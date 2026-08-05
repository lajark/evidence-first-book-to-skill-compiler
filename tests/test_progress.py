"""Tests for the stage-progress callback wiring in the analyze/build pipeline."""

from __future__ import annotations

from pathlib import Path

from book2skill.application.analyze import AnalyzeUseCase
from book2skill.application.progress import (
    STAGE_CANDIDATES,
    STAGE_COMPILE,
    STAGE_EXTRACT,
    STAGE_SKILLS,
    STAGE_STRUCTURE,
    ProgressEvent,
    noop_progress,
)


def _write_txt(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _collect_cb() -> tuple[list[tuple[str, int, int, str]], object]:
    """Return a callback that records every call as a (stage,cur,total,detail)."""
    calls: list[tuple[str, int, int, str]] = []

    def _cb(stage: str, current: int, total: int, detail: str = "") -> None:
        calls.append((stage, current, total, detail))

    return calls, _cb


class _EventCollector:
    """Opt-in reporter seam for structured progress updates."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def __call__(self, stage: str, current: int, total: int, detail: str = "") -> None:
        self.events.append(ProgressEvent(stage, current, total, detail))

    def on_progress_event(self, event: ProgressEvent) -> None:
        self.events.append(event)


class TestAnalyzeProgress:
    """The Analyze pipeline emits a complete, ordered stage sequence."""

    def test_emits_all_stages_in_order(self, tmp_path: Path) -> None:
        f = _write_txt(
            tmp_path / "book.txt",
            "# Chapter One\n\nA paragraph with enough content for the skill.\n\n"
            "# Chapter Two\n\nAnother technique applied in real cases here.",
        )
        use_case = AnalyzeUseCase()
        calls, cb = _collect_cb()
        result = use_case.execute([str(f)], on_progress=cb)  # type: ignore[arg-type]

        assert result.bundle is not None
        stages = [c[0] for c in calls]
        # Stage order: extract ... structure/candidates (interleaved) ... skills.
        assert STAGE_EXTRACT in stages
        assert STAGE_STRUCTURE in stages
        assert STAGE_CANDIDATES in stages
        assert STAGE_SKILLS in stages
        # First call must be the extraction stage.
        assert stages[0] == STAGE_EXTRACT
        # Skills stage comes after all candidate extraction.
        last_cand = max(i for i, s in enumerate(stages) if s == STAGE_CANDIDATES)
        first_skills = min(
            (i for i, s in enumerate(stages) if s == STAGE_SKILLS), default=-1
        )
        assert first_skills > last_cand

    def test_extract_total_matches_file_count(self, tmp_path: Path) -> None:
        f1 = _write_txt(tmp_path / "a.txt", "First file with enough content.")
        f2 = _write_txt(tmp_path / "b.txt", "Second file also has content.")
        use_case = AnalyzeUseCase()
        calls, cb = _collect_cb()
        use_case.execute([str(f1), str(f2)], on_progress=cb)  # type: ignore[arg-type]

        extract_calls = [c for c in calls if c[0] == STAGE_EXTRACT]
        assert len(extract_calls) == 2
        assert extract_calls[0][1] == 1 and extract_calls[0][2] == 2
        assert extract_calls[1][1] == 2 and extract_calls[1][2] == 2
        # Detail carries the file name.
        assert extract_calls[0][3] == "a.txt"
        assert extract_calls[1][3] == "b.txt"

    def test_structure_and_candidates_progress_monotonic(
        self, tmp_path: Path
    ) -> None:
        f = _write_txt(
            tmp_path / "book.txt",
            "# H1\n\nLong enough paragraph one for analysis.\n\n"
            "# H2\n\nLong enough paragraph two for analysis.",
        )
        use_case = AnalyzeUseCase()
        calls, cb = _collect_cb()
        use_case.execute([str(f)], on_progress=cb)  # type: ignore[arg-type]

        for stage in (STAGE_STRUCTURE, STAGE_CANDIDATES):
            stage_calls = [c for c in calls if c[0] == stage]
            assert stage_calls, f"expected calls for {stage}"
            totals = {c[2] for c in stage_calls}
            assert len(totals) == 1, "total must be stable within a stage"
            currents = [c[1] for c in stage_calls]
            assert currents == sorted(currents), "current must be monotonic"
            # A leading ``0/total`` "stage started" ping is emitted before the
            # slow LLM bulk call; per-item completion pings then run 1..total.
            assert currents[0] in (0, 1) and currents[-1] == totals.pop()

    def test_chunk_completion_progress_is_not_replayed_after_bulk_call(
        self, tmp_path: Path
    ) -> None:
        f = _write_txt(tmp_path / "book.txt", "word " * 2_000)
        use_case = AnalyzeUseCase()
        collector = _EventCollector()

        use_case.execute([str(f)], on_progress=collector)

        completed = [
            event
            for event in collector.events
            if event.stage == STAGE_STRUCTURE and event.status == "completed"
        ]
        assert [event.current for event in completed] == list(
            range(1, completed[0].total + 1)
        )
        assert all(event.work_completed is not None for event in completed)
        assert all(event.work_total is not None for event in completed)
        assert completed[-1].work_completed == completed[-1].work_total

    def test_structure_start_uses_matching_timing_history_for_eta(
        self, tmp_path: Path
    ) -> None:
        f = _write_txt(tmp_path / "book.txt", "word " * 1_000)
        data_home = tmp_path / "data"
        AnalyzeUseCase(data_home=data_home).execute([str(f)])
        collector = _EventCollector()

        AnalyzeUseCase(data_home=data_home).execute([str(f)], on_progress=collector)

        started = next(
            event
            for event in collector.events
            if event.stage == STAGE_STRUCTURE and event.status == "started"
        )
        assert started.eta_seconds is not None
        assert started.eta_lower_seconds is not None
        assert started.eta_upper_seconds is not None

    def test_single_real_request_projects_structure_and_skill_work(
        self, tmp_path: Path
    ) -> None:
        import time

        from book2skill.llm.chunking import ChunkItem
        from book2skill.llm.mock_adapter import MockLLMAdapter
        from book2skill.llm.runtime import LLMRuntimeConfig, RuntimeLLMAdapter

        delegate = MockLLMAdapter()

        class _SlowProvider:
            def analyze_chunk(
                self, source_id: str, items: list[ChunkItem]
            ) -> dict[str, list[dict[str, object]]]:
                time.sleep(0.6)
                return delegate.analyze_chunk(source_id, items)

            def suggest_skills(
                self, source_id: str, candidates: list[dict[str, object]]
            ) -> list[dict[str, object]]:
                time.sleep(0.6)
                return delegate.suggest_skills(source_id, candidates)

        adapter = RuntimeLLMAdapter(
            LLMRuntimeConfig(provider="openai", model="demo", api_key="test")
        )
        adapter._provider = _SlowProvider()  # noqa: SLF001 - timing seam
        source = _write_txt(
            tmp_path / "single.txt",
            "A traceable principle with enough detail to become a candidate.",
        )
        collector = _EventCollector()

        AnalyzeUseCase(llm=adapter).execute([str(source)], on_progress=collector)

        for stage in (STAGE_STRUCTURE, STAGE_SKILLS):
            projected = [
                event
                for event in collector.events
                if event.stage == stage
                and event.mode == "estimated"
                and event.work_completed is not None
                and event.work_total is not None
                and 0 < event.work_completed < event.work_total
            ]
            assert projected, f"expected projected heartbeat for {stage}"

    def test_skills_detail_carries_source_id(self, tmp_path: Path) -> None:
        f = _write_txt(
            tmp_path / "book.txt",
            "# Title\n\nEnough content here to produce a candidate unit.",
        )
        use_case = AnalyzeUseCase()
        calls, cb = _collect_cb()
        result = use_case.execute([str(f)], on_progress=cb)  # type: ignore[arg-type]
        assert result.bundle is not None
        source_id = result.bundle.source_ids[0]

        skills_calls = [c for c in calls if c[0] == STAGE_SKILLS]
        assert skills_calls
        # The leading ``0/total`` ping carries no source id; the per-source
        # completion pings (current > 0) carry the source id being processed.
        per_source = [c for c in skills_calls if c[1] > 0]
        assert per_source
        assert all(c[3] == source_id for c in per_source)

    def test_no_callback_runs_silently(self, tmp_path: Path) -> None:
        """Omitting on_progress must not raise (default noop)."""
        f = _write_txt(tmp_path / "book.txt", "Content enough for the pipeline.")
        use_case = AnalyzeUseCase()
        result = use_case.execute([str(f)])
        assert result.bundle is not None

    def test_noop_progress_is_a_noop(self) -> None:
        noop_progress(STAGE_EXTRACT, 1, 1, "x")  # must not raise


class TestBuildProgress:
    """Full Build adds a compile stage after the analyze stages."""

    def test_build_emits_compile_stage(self, tmp_path: Path) -> None:
        from book2skill.application.build import BuildUseCase
        from book2skill.compiler import SkillSpec

        f = _write_txt(
            tmp_path / "book.txt",
            "# Skill Topic\n\nDetailed content that yields candidate units here.",
        )
        use_case = BuildUseCase()
        calls, cb = _collect_cb()
        spec = SkillSpec(
            name="test-skill",
            description="A skill compiled for progress testing.",
            use_when=["when testing progress"],
        )
        result = use_case.build_from_sources(
            [str(f)],
            spec,
            output_dir=tmp_path / "out",
            on_progress=cb,  # type: ignore[arg-type]
        )
        assert result.skill_dir is not None
        stages = [c[0] for c in calls]
        assert STAGE_COMPILE in stages
        # Compile comes after the analyze stages.
        last_analyze = max(
            i
            for i, s in enumerate(stages)
            if s in (STAGE_EXTRACT, STAGE_STRUCTURE, STAGE_CANDIDATES, STAGE_SKILLS)
        )
        first_compile = stages.index(STAGE_COMPILE)
        assert first_compile > last_analyze
        # Compile reports 0/1 then 1/1.
        compile_calls = [c for c in calls if c[0] == STAGE_COMPILE]
        assert (compile_calls[0][1], compile_calls[0][2]) == (0, 1)
        assert (compile_calls[-1][1], compile_calls[-1][2]) == (1, 1)
