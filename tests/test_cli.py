"""Smoke tests for the CLI."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from book2skill.cli import app

runner = CliRunner()


def test_progress_uses_indeterminate_state_for_single_pending_step() -> None:
    from book2skill.cli import _ProgressCtx

    context = _ProgressCtx("Working...")
    with context as report:
        report("structure", 0, 1, "")
        task = context._progress.tasks[0]  # noqa: SLF001 - renderer state seam
        assert task.total is None

        report("structure", 1, 1, "")
        assert task.total == 1
        assert task.completed == 1


def test_token_weighted_single_chunk_starts_indeterminate_then_estimates() -> None:
    from book2skill.application.progress import ProgressEvent
    from book2skill.cli import _ProgressCtx

    context = _ProgressCtx("Working...")
    with context as report:
        on_event = report.on_progress_event  # type: ignore[attr-defined]
        on_event(
            ProgressEvent(
                "structure",
                0,
                1,
                work_completed=0,
                work_total=787,
                eta_seconds=24,
                eta_lower_seconds=12,
                eta_upper_seconds=90,
                eta_sample_count=0,
            )
        )
        task = context._progress.tasks[0]  # noqa: SLF001 - renderer state seam
        assert task.total is None
        assert "冷启动粗估" in task.fields["eta"]

        on_event(
            ProgressEvent(
                "structure",
                0,
                1,
                mode="estimated",
                work_completed=120,
                work_total=787,
                eta_seconds=20,
                eta_lower_seconds=10,
                eta_upper_seconds=80,
                eta_sample_count=0,
            )
        )
        assert task.total == 787
        assert task.completed == 120


def test_progress_renders_structured_rate_limit_status() -> None:
    from book2skill.application.progress import ProgressEvent
    from book2skill.cli import _ProgressCtx

    context = _ProgressCtx("Working...")
    with context as report:
        on_event = report.on_progress_event  # type: ignore[attr-defined]
        on_event(
            ProgressEvent(
                "structure", 0, 1, "source-1", status="rate_limited"
            )
        )
        task = context._progress.tasks[0]  # noqa: SLF001 - renderer state seam
        assert "等待限流" in task.description


def test_progress_uses_token_weighted_work_and_historical_eta() -> None:
    from book2skill.application.progress import ProgressEvent
    from book2skill.cli import _ProgressCtx

    context = _ProgressCtx("Working...")
    with context as report:
        on_event = report.on_progress_event  # type: ignore[attr-defined]
        on_event(
            ProgressEvent(
                "structure",
                1,
                4,
                work_completed=250,
                work_total=1_000,
                eta_seconds=12,
                eta_lower_seconds=8,
                eta_upper_seconds=16,
            )
        )
        task = context._progress.tasks[0]  # noqa: SLF001 - renderer state seam
        assert task.completed == 250
        assert task.total == 1_000
        assert task.fields["eta"] == "预计剩余：8s–16s"


def test_progress_renders_overall_pipeline_task() -> None:
    from book2skill.application.progress import ProgressEvent
    from book2skill.cli import _ProgressCtx

    context = _ProgressCtx("Working...")
    with context as report:
        on_event = report.on_progress_event  # type: ignore[attr-defined]
        on_event(
            ProgressEvent(
                "structure",
                1,
                4,
                overall_completed=42.0,
                overall_total=100.0,
            )
        )
        overall = context._progress.tasks[1]  # noqa: SLF001 - renderer seam
        assert overall.completed == 42.0
        assert overall.total == 100.0


def test_hello() -> None:
    result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert "book2skill" in result.output


def test_cli_locale_switches_human_output() -> None:
    result = runner.invoke(app, ["--locale", "en", "hello"])

    assert result.exit_code == 0
    assert "ready for M0" in result.output


def test_cli_records_effective_locale_in_analysis_manifest(tmp_path) -> None:
    source = tmp_path / "locale.txt"
    source.write_text("A principle with a source.", encoding="utf-8")

    result = runner.invoke(app, ["--locale", "en", "analyze", str(source), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["analysis_run"]["locale"] == "en"


def test_analyze_saves_source_named_bundles_without_overwriting(
    tmp_path, monkeypatch
) -> None:
    """The default user-facing output layout keeps bundles out of the root."""
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "输入资料.txt"
    source.write_text("A principle with a source.", encoding="utf-8")

    first = runner.invoke(app, ["analyze", str(source), "--json"])
    second = runner.invoke(app, ["analyze", str(source), "--json"])

    assert first.exit_code == 0, first.stdout
    assert second.exit_code == 0, second.stdout
    bundle_dir = tmp_path / "output" / "bundles"
    assert (bundle_dir / "bundle_输入资料.json").is_file()
    assert (bundle_dir / "bundle_输入资料_2.json").is_file()
    first_bundle = json.loads((bundle_dir / "bundle_输入资料.json").read_text("utf-8"))
    assert first_bundle["source_ids"]
    raw_manifest = (
        tmp_path
        / "output"
        / "workspace"
        / "raw"
        / first_bundle["source_ids"][0]
        / "1"
        / "manifest.json"
    )
    assert raw_manifest.is_file()


def test_analyze_bundle_write_failure_keeps_json_stdout_parseable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("A principle with a source.", encoding="utf-8")
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("occupied", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "analyze",
            str(source),
            "--bundle-dir",
            str(not_a_directory),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "OUTPUT_WRITE_FAILED"


def test_cli_does_not_accept_api_keys_in_process_arguments() -> None:
    result = runner.invoke(app, ["analyze", "sample.txt", "--llm-api-key", "secret"])

    assert result.exit_code != 0
    # Click/Typer may keep parser diagnostics on stderr in CI runners while
    # older versions mixed them into ``Result.output``.
    diagnostic = result.output + getattr(result, "stderr", "")
    assert "--llm-api-key" in diagnostic


def test_cli_real_adapter_wires_data_home_cache_and_timing_history(
    monkeypatch, tmp_path
) -> None:
    from book2skill.cli import _build_llm_adapter
    from book2skill.llm.runtime import RuntimeLLMAdapter

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    adapter = _build_llm_adapter("compatible", model="demo", data_home=tmp_path)

    assert isinstance(adapter, RuntimeLLMAdapter)
    assert adapter._cache_root == tmp_path / ".cache" / "llm"  # noqa: SLF001
    assert adapter._timing_history._path == (  # noqa: SLF001
        tmp_path / ".cache" / "performance-history.json"
    )


def test_analyze_json_failure_keeps_stdout_parseable(tmp_path) -> None:
    missing = tmp_path / "missing.txt"

    result = runner.invoke(app, ["analyze", str(missing), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "NO_VALID_SOURCES"
    assert "ERROR" not in result.stdout


def test_analyze_llm_failure_emits_json_error(monkeypatch, tmp_path) -> None:
    """A runtime LLM failure must surface as JSON, not an empty stdout."""
    source = tmp_path / "book.txt"
    source.write_text("Content enough for the pipeline.", encoding="utf-8")

    from book2skill.application.analyze import AnalyzeUseCase
    from book2skill.llm.runtime import LLMRuntimeError

    def _raise(self: AnalyzeUseCase, *args: object, **kwargs: object) -> None:
        raise LLMRuntimeError("OpenAI-compatible request failed") from RuntimeError(
            "401 invalid_api_key"
        )

    monkeypatch.setattr(AnalyzeUseCase, "execute", _raise)
    result = runner.invoke(app, ["analyze", str(source), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "LLM_FAILURE"
    assert "invalid_api_key" in payload["error"]["message"]
    assert payload["error"]["recovery"]
    # Human diagnostic routed to stderr so stdout stays a clean JSON document.
    assert "ERROR" in result.stderr


def test_build_json_failure_keeps_stdout_parseable(tmp_path) -> None:
    missing = tmp_path / "missing.txt"

    result = runner.invoke(
        app,
        [
            "build",
            str(missing),
            "--name",
            "json-demo",
            "--description",
            "JSON output regression test.",
            "--use-when",
            "When testing machine output.",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "NO_VALID_SOURCES"
    assert "WARN" not in result.stdout
