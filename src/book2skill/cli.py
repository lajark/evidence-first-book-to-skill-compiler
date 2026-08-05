"""Command-line interface for Book2Skill."""

from __future__ import annotations

import re
import sys
import types
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from book2skill import __version__
from book2skill.application.progress import ProgressEvent, ProgressReporter
from book2skill.config import Locale, load_env_file, resolve_locale
from book2skill.extensions.cli import extensions_app
from book2skill.llm.profiles import ProviderProfileSet
from book2skill.llm.quality import QualityService

if TYPE_CHECKING:
    from book2skill.application.models import AnalysisBundle
    from book2skill.llm.ports import LLMAdapter

app = typer.Typer(name="book2skill", help="Book2Skill CLI")
_active_locale: ContextVar[Locale] = ContextVar("book2skill_locale", default="zh-CN")

#: Runtime artefacts live below one visible root by default.  Paths remain
#: relative to the command's current working directory so a caller can choose
#: a project root simply by changing directory before invoking the CLI.
_DEFAULT_OUTPUT_ROOT = Path("output")
_DEFAULT_BUNDLE_DIR = _DEFAULT_OUTPUT_ROOT / "bundles"
_DEFAULT_DATA_HOME = _DEFAULT_OUTPUT_ROOT / "workspace"

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_MAX_BUNDLE_COMPONENT_LENGTH = 120


def __getattr__(name: str) -> object:
    """Keep the historic CLI test/integration hook lazy-compatible.

    ``UpdateUseCase`` used to be a module global. Returning it only on
    explicit attribute access avoids loading the update pipeline for ordinary
    CLI startup while preserving integrations that instrument the use case.
    """
    if name == "UpdateUseCase":
        from book2skill.application.update import UpdateUseCase

        return UpdateUseCase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@app.callback()
def main(
    locale: str | None = typer.Option(
        None,
        "--locale",
        help="Human-readable output locale: zh-CN (default) or en.",
    ),
) -> None:
    """Configure process-local CLI presentation settings."""
    try:
        resolved = resolve_locale(locale, env_file=load_env_file())
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--locale") from exc
    _active_locale.set(resolved)


def _write_stdout_utf8(text: str) -> None:
    """Write *text* to stdout as UTF-8, independent of the console code page.

    ``sys.stdout.write`` re-encodes through the platform default (GBK on
    Windows), which raises ``UnicodeEncodeError`` for characters it cannot
    represent (e.g. U+2022) when dumping ``ensure_ascii=False`` JSON. Writing
    UTF-8 bytes to the underlying buffer (when present) avoids that. Falls back
    to text writes for in-memory streams without a ``.buffer`` (e.g. capsys).
    """
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        sys.stdout.write(text)


def _write_json(payload: object) -> None:
    """Emit exactly one JSON document to stdout."""
    import json

    _write_stdout_utf8(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _safe_bundle_component(value: str) -> str:
    """Return a readable filename component valid on Windows and POSIX.

    Keep Unicode letters (including Chinese source filenames) intact, but
    remove characters which would make the same command fail on Windows.
    """
    cleaned = _INVALID_FILENAME_CHARS.sub("_", value).strip(". ")
    if not cleaned or not cleaned.strip("_"):
        return "analysis"
    if cleaned.upper() in _WINDOWS_RESERVED_FILENAMES:
        cleaned += "_"
    return cleaned[:_MAX_BUNDLE_COMPONENT_LENGTH].rstrip(". ") or "analysis"


def _bundle_stem(sources: list[Path], collection_id: str) -> str:
    """Derive a readable bundle stem from the supplied source arguments."""
    if not sources:
        return f"bundle_{_safe_bundle_component(collection_id)}"
    first = _safe_bundle_component(sources[0].stem or sources[0].name)
    if len(sources) == 1:
        return f"bundle_{first}"
    return f"bundle_{first}_and_{len(sources) - 1}_more"


def _next_bundle_path(
    bundle_dir: Path, *, sources: list[Path], collection_id: str
) -> Path:
    """Choose a new bundle filename without replacing an earlier run."""
    stem = _bundle_stem(sources, collection_id)
    candidate = bundle_dir / f"{stem}.json"
    suffix = 2
    while candidate.exists():
        candidate = bundle_dir / f"{stem}_{suffix}.json"
        suffix += 1
    return candidate


def _save_analysis_bundle(
    bundle: AnalysisBundle, *, sources: list[Path], bundle_dir: Path
) -> Path:
    """Atomically persist a user-facing AnalysisBundle under *bundle_dir*."""
    from book2skill.storage.file_storage import atomic_write

    bundle_dir.mkdir(parents=True, exist_ok=True)
    target = _next_bundle_path(
        bundle_dir, sources=sources, collection_id=bundle.collection_id
    )
    atomic_write(target, bundle.model_dump_json(indent=2))
    return target


def _exit_output_write_failed(
    exc: OSError, *, json_output: bool, target: Path
) -> None:
    """Report a local output failure while keeping JSON stdout parseable."""
    message = f"Could not write output under {target}: {exc}"
    _print_diagnostic(
        f"[red]ERROR[/red] OUTPUT_WRITE_FAILED: {message}",
        json_output=json_output,
    )
    if json_output:
        _write_json_error(code="OUTPUT_WRITE_FAILED", message=message)
    raise typer.Exit(code=1)


def _write_json_error(
    *, code: str, message: str, recovery: str | None = None
) -> None:
    """Emit a machine-readable error without leaking diagnostics to stdout."""
    error: dict[str, str] = {"code": code, "message": message}
    if recovery:
        error["recovery"] = recovery
    _write_json({"error": error})


def _emit_fatal_error(exc: BaseException, *, json_output: bool) -> None:
    """Emit a fatal pipeline error as structured JSON or human text.

    When ``--json`` is set, an uncaught ``LLMRuntimeError`` or ``DomainError``
    from the run must still produce a parseable JSON document on stdout
    (mirroring ``build``/``update``), instead of leaking a traceback to stderr
    and leaving consumers with an empty stdout file.
    """
    code_attr = getattr(exc, "code", None)
    code = (
        code_attr.value
        if code_attr is not None and hasattr(code_attr, "value")
        else "LLM_FAILURE"
    )
    message = getattr(exc, "message", None) or str(exc)
    cause = exc.__cause__
    if cause is not None:
        message = f"{message}: {cause}"
    recovery = getattr(exc, "recovery", "") or ""
    if code == "LLM_FAILURE" and not recovery:
        recovery = (
            "Check BOOK2SKILL_LLM / LLM_API_KEY / LLM_MODEL / LLM_BASE_URL "
            "and retry, or pass --allow-llm-fallback to use the offline Mock."
        )
    diagnostic = f"[red]ERROR[/red] {code}: {message}"
    if recovery:
        diagnostic += f" (recovery: {recovery})"
    _print_diagnostic(diagnostic, json_output=json_output)
    if json_output:
        _write_json_error(code=code, message=message, recovery=recovery)


def _print_diagnostic(message: str, *, json_output: bool) -> None:
    """Route human diagnostics away from a JSON command's stdout channel."""
    ( _stderr_console if json_output else console).print(message)
app.add_typer(extensions_app)
console = Console()

# Progress goes to stderr so ``--json`` stdout stays clean for piping.
_stderr_console = Console(stderr=True)

#: Map stage codes to localized labels for the progress bar.
_STAGE_LABELS: dict[Locale, dict[str, str]] = {
    "zh-CN": {
        "extract": "提取文本",
        "structure": "结构分析 (LLM)",
        "candidates": "候选抽取 (LLM)",
        "synthesis": "分层综合 (LLM)",
        "skills": "Skill 建议 (LLM)",
        "compile": "编译 Skill",
    },
    "en": {
        "extract": "Extracting text",
        "structure": "Analyzing structure (LLM)",
        "candidates": "Extracting candidates (LLM)",
        "synthesis": "Hierarchical synthesis (LLM)",
        "skills": "Suggesting skills (LLM)",
        "compile": "Compiling skill",
    },
}

_STATUS_LABELS: dict[Locale, dict[str, str]] = {
    "zh-CN": {
        "cached": "缓存命中",
        "rate_limited": "等待限流",
        "retrying": "正在重试",
        "failed": "请求失败",
    },
    "en": {
        "cached": "cache hit",
        "rate_limited": "rate limited",
        "retrying": "retrying",
        "failed": "request failed",
    },
}


def _stage_label(stage: str, detail: str, status: str = "running") -> str:
    """Build the progress-bar description for a stage step."""
    label = _STAGE_LABELS[_active_locale.get()].get(stage, stage)
    if detail:
        label = f"{label}: {detail}"
    status_label = _STATUS_LABELS[_active_locale.get()].get(status)
    if status_label:
        return f"{label} ({status_label})"
    return label


def _format_duration(seconds: float) -> str:
    """Format a short duration without exposing implementation metrics."""
    rounded = max(0, round(seconds))
    minutes, remainder = divmod(rounded, 60)
    if minutes:
        return f"{minutes}m {remainder:02d}s"
    return f"{remainder}s"


def _eta_label(event: ProgressEvent) -> str:
    """Render historical/session ETA as a range, or disclose cold start."""
    locale = _active_locale.get()
    if event.eta_seconds is None:
        return "预计剩余：估算中" if locale == "zh-CN" else "ETA: estimating"
    lower = event.eta_lower_seconds
    upper = event.eta_upper_seconds
    if lower is not None and upper is not None:
        value = (
            _format_duration(lower)
            if round(lower) == round(upper)
            else f"{_format_duration(lower)}–{_format_duration(upper)}"
        )
    else:
        value = f"~{_format_duration(event.eta_seconds)}"
    if event.eta_sample_count == 0:
        value += "（冷启动粗估）" if locale == "zh-CN" else " (cold-start estimate)"
    return f"预计剩余：{value}" if locale == "zh-CN" else f"ETA: {value}"


class _ProgressCtx:
    """Context manager that drives a Rich progress bar on stderr.

    When stdout is not a TTY (CI, redirect) Rich disables live rendering and
    prints periodic lines instead, so progress still surfaces without garbling
    captured output. The progress bar always targets stderr.
    """

    def __init__(self, title: str) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(compact=True),
            TextColumn("[dim]{task.fields[eta]}[/dim]"),
            console=_stderr_console,
            transient=False,
        )
        self._title = title
        self._task: int | None = None

    def __enter__(self) -> ProgressReporter:
        self._progress.__enter__()
        self._task = self._progress.add_task(
            self._title,
            total=None,
            eta=_eta_label(ProgressEvent("", 0, 0)),
        )
        task_id = self._task
        active_stage: str | None = None

        def _render(event: ProgressEvent) -> None:
            nonlocal active_stage, task_id
            assert task_id is not None
            description = _stage_label(event.stage, event.detail, event.status)
            completed = (
                int(event.work_completed)
                if event.work_completed is not None
                else event.current
            )
            total = (
                int(event.work_total)
                if event.work_total is not None
                else event.total
            )
            if event.mode == "indeterminate":
                determinate_total: int | None = None
            elif event.mode in {"estimated", "determinate"}:
                determinate_total = max(total, 1)
            else:
                determinate_total = (
                    None
                    if event.current == 0 and event.total <= 1
                    else max(total, 1)
                )
            if event.stage != active_stage:
                active_stage = event.stage
                self._progress.remove_task(task_id)
                task_id = self._progress.add_task(
                    description,
                    total=determinate_total,
                    completed=completed,
                    eta=_eta_label(event),
                )
                self._task = task_id
                return
            self._progress.update(
                task_id,
                description=description,
                total=determinate_total,
                completed=completed,
                eta=_eta_label(event),
            )

        def _report(stage: str, current: int, total: int, detail: str = "") -> None:
            _render(ProgressEvent(stage, current, total, detail))

        _report.on_progress_event = _render  # type: ignore[attr-defined]

        return _report

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self._progress.__exit__(exc_type, exc_val, exc_tb)


def _build_llm_adapter(
    kind: str | None,
    *,
    model: str | None = None,
    base_url: str | None = None,
    allow_fallback: bool = False,
    data_home: Path | None = None,
    llm_profiles: str | None = None,
    llm_profile: str | None = None,
    llm_strategy: str = "single",
) -> LLMAdapter:
    """Resolve the shared runtime and build its auditable adapter.

    ``balanced`` strategy fans Map work across the profiles in
    ``llm_profiles``; it requires the profile set. The ``single`` strategy
    builds one adapter from ``llm_profile`` (or the legacy env resolution).
    Credentials are always read from profile env-var names or the environment
    and never enter argv, config, manifests, caches or logs.
    """
    from book2skill.llm.profiles import (
        load_profile_set,
        profile_to_runtime_config,
    )
    from book2skill.llm.router import RouterLLMAdapter
    from book2skill.llm.runtime import (
        LLMRuntimeError,
        build_llm_adapter,
        default_timing_history_path,
        resolve_runtime_config,
    )

    env_file = load_env_file()
    if llm_strategy == "balanced":
        if not llm_profiles:
            raise typer.BadParameter(
                "--llm-strategy balanced requires --llm-profiles <path>."
            )
        try:
            profile_set = load_profile_set(llm_profiles)
        except (LLMRuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        return RouterLLMAdapter(
            profile_set,
            allow_fallback=allow_fallback,
            locale=_active_locale.get(),
            data_home=data_home,
            env_file=env_file,
        )

    try:
        if llm_profiles:
            profile_set = load_profile_set(llm_profiles)
            try:
                profile = profile_set.profile(llm_profile)
            except KeyError as exc:
                raise typer.BadParameter(str(exc)) from exc
            config = profile_to_runtime_config(
                profile,
                allow_fallback=allow_fallback,
                locale=_active_locale.get(),
                env_file=env_file,
            )
        else:
            config = resolve_runtime_config(
                kind,
                model=model,
                base_url=base_url,
                allow_fallback=allow_fallback,
                locale=_active_locale.get(),
                env_file=env_file,
            )
    except (LLMRuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    cache_root = data_home / ".cache" / "llm" if data_home is not None else None
    timing_history_path = None
    if config.provider == "openai":
        timing_history_path = (
            data_home / ".cache" / "performance-history.json"
            if data_home is not None
            else default_timing_history_path()
        )
    return build_llm_adapter(
        config,
        cache_root=cache_root,
        timing_history_path=timing_history_path,
    )


def _build_quality_service(
    *,
    enabled: bool,
    mode: str,
    llm_profiles: str | None,
    quality_authorization: str | None,
    budget_calls: int | None,
    budget_cost: float | None,
    critic_profile: str | None,
    arbiter_profile: str | None,
    allow_fallback: bool,
    data_home: Path | None,
) -> QualityService | None:
    """Build the selective quality reviewer, or ``None`` when disabled.

    ``maximum`` mode requires explicit authorization and a hard budget; the
    service is never constructed without them (fail-closed). Critics/Arbiter
    are built from the specified profile ids (or the first ``critic`` role and
    the ``default_profile`` respectively).
    """
    from book2skill.llm.profiles import (
        load_profile_set,
        profile_to_runtime_config,
    )
    from book2skill.llm.quality import (
        LLMReviewer,
        QualityBudget,
        QualityService,
    )
    from book2skill.llm.runtime import RuntimeLLMAdapter

    if not enabled:
        return None
    if mode not in {"standard", "maximum"}:
        raise typer.BadParameter("--quality-mode must be 'standard' or 'maximum'")
    if mode == "maximum":
        if not quality_authorization:
            raise typer.BadParameter(
                "--quality-mode maximum requires --quality-authorization."
            )
        if budget_calls is None and budget_cost is None:
            raise typer.BadParameter(
                "--quality-mode maximum requires --quality-budget-calls and/or "
                "--quality-budget-cost."
            )
    if not llm_profiles:
        raise typer.BadParameter("--quality requires --llm-profiles <path>.")
    profile_set = load_profile_set(llm_profiles)
    cache_root = data_home / ".cache" / "llm" if data_home is not None else None

    def build_reviewer(profile_id: str, role: str) -> LLMReviewer:
        profile = profile_set.profile(profile_id)
        config = profile_to_runtime_config(
            profile,
            allow_fallback=allow_fallback,
            locale=_active_locale.get(),
            env_file=load_env_file(),
        )
        adapter = RuntimeLLMAdapter(config, cache_root=cache_root)
        return LLMReviewer(adapter, role=role, model_id=profile.profile_id)  # type: ignore[arg-type]

    critic_id = critic_profile or _first_critic_profile(profile_set)
    arbiter_id = arbiter_profile or profile_set.default_profile
    budget = QualityBudget(max_calls=budget_calls, max_cost=budget_cost)
    return QualityService(
        critic_reviewer=build_reviewer(critic_id, "critic"),
        arbiter_reviewer=build_reviewer(arbiter_id, "arbiter"),
        budget=budget,
    )


def _first_critic_profile(
    profile_set: ProviderProfileSet,
) -> str:
    """Return the first profile able to serve the ``critic`` role."""
    for profile in profile_set.profiles:
        if profile.can_serve("critic"):
            return profile.profile_id
    return profile_set.default_profile


@app.command(name="hello")
def hello() -> None:
    """Placeholder hello command for M0."""
    ready = "ready for M0" if _active_locale.get() == "en" else "已就绪（M0）"
    console.print(f"book2skill v{__version__} — {ready}")


@app.command(name="version")
def version() -> None:
    """Show version."""
    console.print(f"book2skill {__version__}")


@app.command(name="analyze")
def analyze(
    sources: list[Path] = typer.Argument(
        ...,
        help="Source files, directories, or glob patterns to analyze.",
    ),
    collection_id: str | None = typer.Option(
        None,
        "--collection-id",
        "-c",
        help="Optional collection identifier (auto-derived when omitted).",
    ),
    rights_note: str | None = typer.Option(
        None,
        "--rights-note",
        help="Rights-confirmation note recorded on each manifest.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the AnalysisBundle as JSON to stdout.",
    ),
    bundle_dir: Path = typer.Option(
        _DEFAULT_BUNDLE_DIR,
        "--bundle-dir",
        help="Directory for saved AnalysisBundles (default: output/bundles).",
    ),
    data_home: Path = typer.Option(
        _DEFAULT_DATA_HOME,
        "--data-home",
        help="Directory for raw/schema/cache data (default: output/workspace).",
    ),
    llm: str | None = typer.Option(
        None,
        "--llm",
        help="LLM adapter: 'mock' (offline, default) or 'openai'/'compatible' "
        "(any OpenAI-compatible endpoint). "
        "Falls back to BOOK2SKILL_LLM env var / .env file.",
    ),
    llm_model: str | None = typer.Option(
        None,
        "--llm-model",
        help="Model name (e.g. gpt-4o, qwen-plus). "
        "Falls back to LLM_MODEL / OPENAI_MODEL env var / .env file.",
    ),
    llm_base_url: str | None = typer.Option(
        None,
        "--llm-base-url",
        help="Base URL for an OpenAI-compatible endpoint "
        "(e.g. http://localhost:11434/v1 or "
        "https://dashscope.aliyuncs.com/compatible-mode/v1). "
        "Falls back to LLM_BASE_URL / OPENAI_BASE_URL env var / .env file.",
    ),
    allow_llm_fallback: bool = typer.Option(
        False,
        "--allow-llm-fallback",
        help="Explicitly permit deterministic Mock fallback if a real LLM fails.",
    ),
    llm_profiles: str | None = typer.Option(
        None,
        "--llm-profiles",
        help="Path to a multi-provider profile-set YAML. Each profile reads its "
        "credential from a named env var; keys never enter the file.",
    ),
    llm_profile: str | None = typer.Option(
        None,
        "--llm-profile",
        help="Select one profile_id from the --llm-profiles set "
        "(default: the set's default_profile).",
    ),
    llm_strategy: str = typer.Option(
        "single",
        "--llm-strategy",
        help="Routing strategy: 'single' (default) or 'balanced' (multi-channel "
        "Map fan-out; requires --llm-profiles).",
    ),
    quality: bool = typer.Option(
        False,
        "--quality",
        help="Run selective quality review (Critic/Arbiter) after analysis. "
        "Requires --llm-profiles.",
    ),
    quality_mode: str = typer.Option(
        "standard",
        "--quality-mode",
        help="Quality mode: 'standard' (default) or 'maximum' (requires "
        "--quality-authorization and a hard budget).",
    ),
    quality_authorization: str | None = typer.Option(
        None,
        "--quality-authorization",
        help="Explicit authorization string required for --quality-mode maximum.",
    ),
    quality_budget_calls: int | None = typer.Option(
        None,
        "--quality-budget-calls",
        help="Hard cap on quality review calls.",
    ),
    quality_budget_cost: float | None = typer.Option(
        None,
        "--quality-budget-cost",
        help="Hard cap on estimated quality review cost (USD).",
    ),
    quality_critic_profile: str | None = typer.Option(
        None,
        "--quality-critic-profile",
        help="profile_id used as the quality Critic.",
    ),
    quality_arbiter_profile: str | None = typer.Option(
        None,
        "--quality-arbiter-profile",
        help="profile_id used as the quality Arbiter.",
    ),
) -> None:
    """Analyze sources without generating a final Skill (FR-03-1)."""
    from book2skill.application.analyze import AnalyzeUseCase
    from book2skill.domain.errors import DomainError
    from book2skill.llm.runtime import LLMRuntimeError

    adapter = _build_llm_adapter(
        llm,
        model=llm_model,
        base_url=llm_base_url,
        allow_fallback=allow_llm_fallback,
        data_home=data_home,
        llm_profiles=llm_profiles,
        llm_profile=llm_profile,
        llm_strategy=llm_strategy,
    )
    quality_service = _build_quality_service(
        enabled=quality,
        mode=quality_mode,
        llm_profiles=llm_profiles,
        quality_authorization=quality_authorization,
        budget_calls=quality_budget_calls,
        budget_cost=quality_budget_cost,
        critic_profile=quality_critic_profile,
        arbiter_profile=quality_arbiter_profile,
        allow_fallback=allow_llm_fallback,
        data_home=data_home,
    )
    use_case = AnalyzeUseCase(data_home=data_home, llm=adapter)
    try:
        with _ProgressCtx("Analyzing...") as on_progress:
            result = use_case.execute(
                [str(s) for s in sources],
                collection_id=collection_id,
                rights_note=rights_note,
                on_progress=on_progress,
                quality=quality_service,
            )
    except (LLMRuntimeError, DomainError) as exc:
        _emit_fatal_error(exc, json_output=json_output)
        raise typer.Exit(code=1) from exc

    if result.errors:
        for err in result.errors:
            _print_diagnostic(
                f"[red]ERROR[/red] {err.code.value}: {err.message} "
                f"(recovery: {err.recovery})",
                json_output=json_output,
            )

    if result.bundle is None:
        if json_output:
            _write_json_error(
                code="NO_VALID_SOURCES",
                message="No valid sources to analyze.",
            )
        else:
            console.print("[red]No valid sources to analyze.[/red]")
        raise typer.Exit(code=1)

    try:
        bundle_path = _save_analysis_bundle(
            result.bundle, sources=sources, bundle_dir=bundle_dir
        )
    except OSError as exc:
        _exit_output_write_failed(exc, json_output=json_output, target=bundle_dir)

    if json_output:
        _write_json(result.bundle.model_dump(mode="json"))
    else:
        b = result.bundle
        console.print(
            f"[green]Analysis complete[/green] — collection: {b.collection_id}"
        )
        console.print(f"  sources:          {len(b.source_ids)}")
        console.print(f"  structure entries: {len(b.structure)}")
        console.print(f"  candidate units:  {len(b.candidate_units)}")
        console.print(f"  review queue:     {len(b.review_queue)}")
        console.print(f"  conflicts:        {len(b.conflicts)}")
        console.print(f"  suggested skills: {len(b.suggested_skills)}")
        console.print(f"  analysis bundle:  {bundle_path}")
        if b.review_queue:
            console.print("\n[yellow]Items needing review:[/yellow]")
            for item in b.review_queue:
                console.print(
                    f"  - [{item.severity}] {item.ref_id}: {item.reason}"
                )
        if b.suggested_skills:
            console.print("\n[blue]Suggested skills:[/blue]")
            for skill in b.suggested_skills:
                console.print(f"  - {skill.name}: {skill.description}")


@app.command(name="batch")
def batch(
    sources: list[Path] = typer.Argument(
        ...,
        help="Source files, directories, or glob patterns to batch-analyze.",
    ),
    rights_note: str | None = typer.Option(
        None,
        "--rights-note",
        help="Rights-confirmation note recorded on each manifest.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the BatchResult as JSON to stdout.",
    ),
    bundle_dir: Path = typer.Option(
        _DEFAULT_BUNDLE_DIR,
        "--bundle-dir",
        help="Directory for per-source AnalysisBundles (default: output/bundles).",
    ),
    data_home: Path = typer.Option(
        _DEFAULT_DATA_HOME,
        "--data-home",
        help="Directory for raw/schema/cache data (default: output/workspace).",
    ),
    llm: str | None = typer.Option(
        None,
        "--llm",
        help="LLM adapter: 'mock' (offline, default) or 'openai'/'compatible' "
        "(any OpenAI-compatible endpoint). "
        "Falls back to BOOK2SKILL_LLM env var / .env file.",
    ),
    llm_model: str | None = typer.Option(
        None,
        "--llm-model",
        help="Model name (e.g. gpt-4o, qwen-plus). "
        "Falls back to LLM_MODEL / OPENAI_MODEL env var / .env file.",
    ),
    llm_base_url: str | None = typer.Option(
        None,
        "--llm-base-url",
        help="Base URL for an OpenAI-compatible endpoint "
        "(e.g. http://localhost:11434/v1 or "
        "https://dashscope.aliyuncs.com/compatible-mode/v1). "
        "Falls back to LLM_BASE_URL / OPENAI_BASE_URL env var / .env file.",
    ),
    allow_llm_fallback: bool = typer.Option(
        False,
        "--allow-llm-fallback",
        help="Explicitly permit deterministic Mock fallback if a real LLM fails.",
    ),
    llm_profiles: str | None = typer.Option(
        None,
        "--llm-profiles",
        help="Path to a multi-provider profile-set YAML. Each profile reads its "
        "credential from a named env var; keys never enter the file.",
    ),
    llm_profile: str | None = typer.Option(
        None,
        "--llm-profile",
        help="Select one profile_id from the --llm-profiles set "
        "(default: the set's default_profile).",
    ),
    llm_strategy: str = typer.Option(
        "single",
        "--llm-strategy",
        help="Routing strategy: 'single' (default) or 'balanced' (multi-channel "
        "Map fan-out; requires --llm-profiles).",
    ),
    checkpoint: Path | None = typer.Option(
        None,
        "--checkpoint",
        help="Path to a JSON checkpoint file for resume after interruption.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume from a previous checkpoint: skip files that already succeeded.",
    ),
) -> None:
    """Batch-analyze sources with per-file failure isolation (FR-02)."""
    from book2skill.application.analyze import AnalyzeUseCase
    from book2skill.application.batch import BatchOrchestrator
    from book2skill.domain.errors import DomainError
    from book2skill.llm.runtime import LLMRuntimeError

    adapter = _build_llm_adapter(
        llm,
        model=llm_model,
        base_url=llm_base_url,
        allow_fallback=allow_llm_fallback,
        data_home=data_home,
        llm_profiles=llm_profiles,
        llm_profile=llm_profile,
        llm_strategy=llm_strategy,
    )
    use_case = AnalyzeUseCase(data_home=data_home, llm=adapter)
    orchestrator = BatchOrchestrator(use_case=use_case)

    def _on_progress(index: int, total: int, path: str) -> None:
        if not json_output:
            console.print(
                f"[dim]({index}/{total})[/dim] Processing {Path(path).name} ..."
            )

    try:
        result = orchestrator.execute(
            [str(s) for s in sources],
            rights_note=rights_note,
            on_progress=_on_progress,
            checkpoint_path=checkpoint,
            resume=resume,
        )
    except (LLMRuntimeError, DomainError) as exc:
        _emit_fatal_error(exc, json_output=json_output)
        raise typer.Exit(code=1) from exc

    saved_bundle_paths: dict[int, Path] = {}
    try:
        for index, outcome in enumerate(result.outcomes):
            if outcome.bundle is not None:
                saved_bundle_paths[index] = _save_analysis_bundle(
                    outcome.bundle,
                    sources=[Path(outcome.path)],
                    bundle_dir=bundle_dir,
                )
    except OSError as exc:
        _exit_output_write_failed(exc, json_output=json_output, target=bundle_dir)

    if json_output:
        import json

        batch_json = json.dumps(
            result.model_dump(mode="json"), indent=2, ensure_ascii=False
        )
        _write_stdout_utf8(batch_json + "\n")
    else:
        s = result.summary
        console.print(
            f"[green]Batch complete[/green] — "
            f"total: {s.total}, succeeded: {s.succeeded}, "
            f"partial: {s.partial}, failed: {s.failed}, skipped: {s.skipped}"
        )
        for index, o in enumerate(result.outcomes):
            if o.status == "success" and o.bundle is not None:
                console.print(
                    f"  [green]OK[/green]   {o.path} ({o.source_id})"
                    f" → {saved_bundle_paths[index]}"
                )
            elif o.status == "partial":
                console.print(
                    f"  [yellow]PART[/yellow] {o.path} ({o.source_id})"
                    + (
                        f" → {saved_bundle_paths[index]}"
                        if index in saved_bundle_paths
                        else ""
                    )
                )
            else:
                console.print(f"  [red]FAIL[/red] {o.path} ({o.status})")
        if result.failure_list:
            console.print("\n[red]Failure list:[/red]")
            for err in result.failure_list:
                console.print(
                    f"  - {err.code}: {err.path} — {err.message}"
                    + (f" (recovery: {err.recovery})" if err.recovery else "")
                )

    if result.summary.succeeded == 0 and result.summary.partial == 0:
        raise typer.Exit(code=1)


@app.command(name="build")
def build(
    sources: list[Path] = typer.Argument(
        None,
        help="Source files, directories, or glob patterns (required unless "
        "--from-analysis is given).",
    ),
    from_analysis: Path | None = typer.Option(
        None,
        "--from-analysis",
        help="Path to an AnalysisBundle JSON file (skips extraction).",
    ),
    name: str = typer.Option(
        ...,
        "--name",
        "-n",
        help="Skill slug (lowercase letters, digits, hyphens).",
    ),
    description: str = typer.Option(
        ...,
        "--description",
        "-d",
        help="Skill description (>=10 chars): what it does, when to use it.",
    ),
    use_when: list[str] = typer.Option(
        ...,
        "--use-when",
        help="When to invoke the skill (repeatable).",
    ),
    no_use_when: list[str] = typer.Option(
        None,
        "--no-use-when",
        help="When NOT to invoke the skill (repeatable).",
    ),
    required_input: list[str] = typer.Option(
        None,
        "--required-input",
        help="Required domain input for the generated Skill (repeatable).",
    ),
    output: list[str] = typer.Option(
        None,
        "--output",
        help="Declared domain output for the generated Skill (repeatable).",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Output directory for the Skill (default: output/skills/<name>).",
    ),
    bundle_dir: Path = typer.Option(
        _DEFAULT_BUNDLE_DIR,
        "--bundle-dir",
        help="Directory for the Full Build AnalysisBundle (default: output/bundles).",
    ),
    data_home: Path = typer.Option(
        _DEFAULT_DATA_HOME,
        "--data-home",
        help="Directory for raw/schema/cache data (default: output/workspace).",
    ),
    rights_note: str | None = typer.Option(
        None,
        "--rights-note",
        help="Rights-confirmation note recorded on each manifest (Full Build only).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the BuildResult summary as JSON to stdout.",
    ),
    llm: str | None = typer.Option(
        None,
        "--llm",
        help="LLM adapter: mock (default) or openai/compatible.",
    ),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    allow_llm_fallback: bool = typer.Option(
        False,
        "--allow-llm-fallback",
        help="Explicitly permit deterministic Mock fallback if a real LLM fails.",
    ),
    llm_profiles: str | None = typer.Option(
        None,
        "--llm-profiles",
        help="Path to a multi-provider profile-set YAML. Each profile reads its "
        "credential from a named env var; keys never enter the file.",
    ),
    llm_profile: str | None = typer.Option(
        None,
        "--llm-profile",
        help="Select one profile_id from the --llm-profiles set "
        "(default: the set's default_profile).",
    ),
    llm_strategy: str = typer.Option(
        "single",
        "--llm-strategy",
        help="Routing strategy: 'single' (default) or 'balanced' (multi-channel "
        "Map fan-out; requires --llm-profiles).",
    ),
) -> None:
    """Full Build or Build from Analysis (FR-03-2 / FR-03-3)."""
    from book2skill.application.build import BuildUseCase
    from book2skill.compiler import SkillSpec
    from book2skill.domain.errors import DomainError

    if not sources and from_analysis is None:
        raise typer.BadParameter(
            "Provide SOURCES or use --from-analysis <bundle.json>."
        )
    if sources and from_analysis is not None:
        raise typer.BadParameter(
            "SOURCES and --from-analysis are mutually exclusive."
        )

    try:
        spec = SkillSpec(
            name=name,
            description=description,
            use_when=list(use_when),
            do_not_use_when=list(no_use_when) if no_use_when else [],
            required_inputs=list(required_input) if required_input else [
                "A legally held source document or a verified analysis bundle."
            ],
            outputs=list(output) if output else [
                "A concise, source-traceable response or action plan; "
                "never raw book text."
            ],
        )
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid SkillSpec: {exc}") from exc

    adapter = None
    if sources:
        adapter = _build_llm_adapter(
            llm,
            model=llm_model,
            base_url=llm_base_url,
            allow_fallback=allow_llm_fallback,
            data_home=data_home,
            llm_profiles=llm_profiles,
            llm_profile=llm_profile,
            llm_strategy=llm_strategy,
        )
        assert hasattr(adapter, "config")
    # Pass the full adapter so ``balanced`` routing fans Map work across
    # channels; taking adapter.config would collapse to a single channel.
    use_case = BuildUseCase(data_home=data_home, llm=adapter)

    try:
        with _ProgressCtx("Building skill...") as on_progress:
            if from_analysis is not None:
                result = use_case.build_from_bundle(
                    from_analysis,
                    spec,
                    output_dir=output_dir,
                    on_progress=on_progress,
                )
            else:
                assert sources is not None  # narrowed by the guard above
                result = use_case.build_from_sources(
                    [str(s) for s in sources],
                    spec,
                    rights_note=rights_note,
                    output_dir=output_dir,
                    on_progress=on_progress,
                )
    except DomainError as exc:
        if json_output:
            _print_diagnostic(
                f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
                f"(recovery: {exc.recovery})",
                json_output=True,
            )
            _write_json_error(
                code=exc.code.value, message=exc.message, recovery=exc.recovery
            )
        else:
            console.print(
                f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
                f"(recovery: {exc.recovery})"
            )
        raise typer.Exit(code=1) from exc

    if result.errors:
        for err in result.errors:
            _print_diagnostic(
                f"[yellow]WARN[/yellow] {err.code.value}: {err.message} "
                f"(recovery: {err.recovery})",
                json_output=json_output,
            )

    if result.skill_dir is None:
        if json_output:
            _write_json_error(
                code="NO_VALID_SOURCES",
                message="Build failed: no valid sources analysed.",
            )
        else:
            console.print("[red]Build failed: no valid sources analysed.[/red]")
        raise typer.Exit(code=1)

    bundle_path: Path | None = None
    if sources and result.bundle is not None:
        try:
            bundle_path = _save_analysis_bundle(
                result.bundle, sources=sources, bundle_dir=bundle_dir
            )
        except OSError as exc:
            _exit_output_write_failed(exc, json_output=json_output, target=bundle_dir)

    if json_output:
        payload = {
            "skill_dir": str(result.skill_dir),
            "bundle_path": str(bundle_path) if bundle_path is not None else None,
            "collection_id": result.collection_id,
            "source_count": len(result.source_manifests),
            "warnings": len(result.errors),
            "analysis_run": (
                result.bundle.analysis_run.model_dump(mode="json")
                if result.bundle is not None and result.bundle.analysis_run is not None
                else None
            ),
        }
        _write_json(payload)
    else:
        console.print(
            f"[green]Build complete[/green] — skill: {result.skill_dir}"
        )
        if result.collection_id:
            console.print(f"  collection:       {result.collection_id}")
        if result.source_manifests:
            console.print(
                f"  sources tracked:  {len(result.source_manifests)}"
            )
            for m in result.source_manifests:
                sha_prefix = m.content_sha256[:12]
                console.print(
                    f"    - {m.source_id} ({m.format}) sha256={sha_prefix}..."
                )
        if result.errors:
            console.print(f"  warnings:         {len(result.errors)}")
        console.print(
            "  artifacts:        SKILL.md, references/, assets/, "
            "provenance.yml, quality-report.md"
        )
        if bundle_path is not None:
            console.print(f"  analysis bundle:  {bundle_path}")


@app.command(name="update")
def update(
    skill_dir: Path = typer.Argument(
        ..., help="Published Skill directory to update (or to roll back)."
    ),
    new_sources: list[Path] = typer.Argument(
        None,
        help="New source files, directories or globs to fold in (omit with "
        "--rollback).",
    ),
    data_home: Path = typer.Option(
        ...,
        "--data-home",
        help="Data root with the Schema collection and snapshot store.",
    ),
    collection_id: str | None = typer.Option(
        None,
        "--collection-id",
        "-c",
        help="Collection id override (else read from skill.meta.json).",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Skill slug override (else read from skill.meta.json).",
    ),
    description: str | None = typer.Option(
        None,
        "--description",
        "-d",
        help="Skill description override (else read from skill.meta.json).",
    ),
    use_when: list[str] = typer.Option(
        None,
        "--use-when",
        help="When to invoke (repeatable); overrides skill.meta.json shape.",
    ),
    no_use_when: list[str] = typer.Option(
        None,
        "--no-use-when",
        help="When NOT to invoke (repeatable).",
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help=(
            "Approve clean changed candidates and atomically publish the "
            "folded-in Skill; open review items or conflicts still block. "
            "Default: dry-run plan."
        ),
    ),
    replace_sources: bool = typer.Option(
        False,
        "--replace-sources",
        help=(
            "Treat SOURCES as a complete replacement set; old-only knowledge "
            "may be deprecated. Default fold-in is add-only."
        ),
    ),
    rollback: bool = typer.Option(
        False,
        "--rollback",
        help="Restore the latest published snapshot instead of folding in.",
    ),
    rights_note: str | None = typer.Option(
        None,
        "--rights-note",
        help="Rights-confirmation note recorded on each new manifest.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit structured JSON to stdout."
    ),
    llm: str | None = typer.Option(
        None,
        "--llm",
        help="LLM adapter: mock (default) or openai/compatible.",
    ),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    allow_llm_fallback: bool = typer.Option(
        False,
        "--allow-llm-fallback",
        help="Explicitly permit deterministic Mock fallback if a real LLM fails.",
    ),
    llm_profiles: str | None = typer.Option(
        None,
        "--llm-profiles",
        help="Path to a multi-provider profile-set YAML. Each profile reads its "
        "credential from a named env var; keys never enter the file.",
    ),
    llm_profile: str | None = typer.Option(
        None,
        "--llm-profile",
        help="Select one profile_id from the --llm-profiles set "
        "(default: the set's default_profile).",
    ),
    llm_strategy: str = typer.Option(
        "single",
        "--llm-strategy",
        help="Routing strategy: 'single' (default) or 'balanced' (multi-channel "
        "Map fan-out; requires --llm-profiles).",
    ),
) -> None:
    """Update / Fold-in a published Skill (FR-03-4)."""
    from book2skill.application.update import UpdateUseCase
    from book2skill.compiler import SkillSpec
    from book2skill.domain.errors import DomainError

    if rollback:
        if new_sources:
            raise typer.BadParameter("SOURCES cannot be combined with --rollback.")
        if replace_sources:
            raise typer.BadParameter(
                "--replace-sources cannot be combined with --rollback."
            )
        use_case = UpdateUseCase(data_home)
        try:
            record = use_case.rollback(skill_dir)
        except DomainError as exc:
            _print_diagnostic(
                f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
                f"(recovery: {exc.recovery})",
                json_output=json_output,
            )
            if json_output:
                _write_json_error(
                    code=exc.code.value, message=exc.message, recovery=exc.recovery
                )
            raise typer.Exit(code=1) from exc

        if json_output:
            rb_payload: dict[str, object] = {
                "action": record.action,
                "skill_dir": str(record.skill_dir),
                "snapshot_path": (
                    str(record.snapshot_path) if record.snapshot_path else None
                ),
                "published_at": record.published_at,
            }
            _write_json(rb_payload)
        else:
            console.print(
                f"[green]Rolled back[/green] — skill: {record.skill_dir}"
            )
            if record.snapshot_path:
                console.print(f"  current moved to snapshot: {record.snapshot_path}")
        return

    if not new_sources:
        raise typer.BadParameter("Provide SOURCES or use --rollback.")

    spec: SkillSpec | None = None
    if name and description and use_when:
        try:
            spec = SkillSpec(
                name=name,
                description=description,
                use_when=list(use_when),
                do_not_use_when=list(no_use_when) if no_use_when else [],
            )
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid SkillSpec: {exc}") from exc

    adapter = _build_llm_adapter(
        llm,
        model=llm_model,
        base_url=llm_base_url,
        allow_fallback=allow_llm_fallback,
        data_home=data_home,
        llm_profiles=llm_profiles,
        llm_profile=llm_profile,
        llm_strategy=llm_strategy,
    )
    assert hasattr(adapter, "config")
    # Pass the full adapter so ``balanced`` routing fans Map work across
    # channels; taking adapter.config would collapse to a single channel.
    use_case = UpdateUseCase(data_home, llm=adapter)
    try:
        result = use_case.execute(
            skill_dir,
            [str(s) for s in new_sources],
            spec,
            collection_id=collection_id,
            rights_note=rights_note,
            confirm=confirm,
            replace_sources=replace_sources,
        )
    except DomainError as exc:
        _print_diagnostic(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})",
            json_output=json_output,
        )
        if json_output:
            _write_json_error(
                code=exc.code.value, message=exc.message, recovery=exc.recovery
            )
        raise typer.Exit(code=1) from exc

    s = result.suggestion
    if json_output:
        payload: dict[str, object] = {
            "published": result.published,
            "reason": result.reason,
            "collection_id": result.collection_id,
            "added": len(s.added),
            "modified": len(s.modified),
            "deprecated": len(s.removed),
            "conflicts": len(s.conflicts),
            "new_conflicts": len(s.merge.new_conflicts) if s.merge else 0,
            "analysis_conflicts": len(s.analysis_conflict_ids),
            "review_items": len(s.review_item_ids),
            "analysis_errors": len(s.analysis_errors),
            "applied_overrides": len(s.merge.applied_overrides) if s.merge else 0,
            "analysis_run": (
                s.analysis_run.model_dump(mode="json")
                if s.analysis_run is not None
                else None
            ),
            "preserved_overrides": (
                len(s.merge.preserved_overrides) if s.merge else 0
            ),
        }
        if result.publish_record is not None:
            rec = result.publish_record
            payload["skill_dir"] = str(rec.skill_dir)
            payload["snapshot"] = (
                str(rec.snapshot_path) if rec.snapshot_path else None
            )
            payload["published_at"] = rec.published_at
        _write_json(payload)
        return

    console.print(f"[bold]Update plan[/bold] — {result.skill_dir}")
    console.print(f"  added:      {len(s.added)}")
    for u in s.added:
        console.print(f"    + {u.unit_id} ({u.kind})")
    console.print(f"  modified:   {len(s.modified)}")
    for c in s.modified:
        console.print(f"    ~ {c.unit_id}: {', '.join(c.changed_fields)}")
    console.print(f"  deprecated: {len(s.removed)}")
    for u in s.removed:
        console.print(f"    - {u.unit_id} ({u.kind})")
    console.print(f"  conflicts:  {len(s.conflicts)}")
    for cf in s.conflicts:
        console.print(f"    ! {cf.conflict_id}: {cf.description}")
    if s.merge:
        console.print(
            f"  merge: applied {len(s.merge.applied_overrides)} override(s), "
            f"preserved {len(s.merge.preserved_overrides)}, "
            f"{len(s.merge.new_conflicts)} new conflict(s)"
        )

    if result.published and result.publish_record is not None:
        console.print(
            f"[green]Published[/green] — skill: {result.publish_record.skill_dir}"
        )
        if result.publish_record.snapshot_path:
            console.print(
                f"  snapshot: {result.publish_record.snapshot_path}"
            )
    elif result.reason == "no_changes":
        console.print("[dim]No changes detected; nothing published.[/dim]")
    else:
        console.print("[dim]Dry run. Pass --confirm to publish atomically.[/dim]")


@app.command(name="diff")
def diff(
    old: str = typer.Argument(
        ..., help="Old side: collection_id or AnalysisBundle JSON path."
    ),
    new: str = typer.Argument(
        ..., help="New side: collection_id or AnalysisBundle JSON path."
    ),
    data_home: Path | None = typer.Option(
        None,
        "--data-home",
        help="Data root for collection_id resolution (raw/schema/overrides).",
    ),
    merge: bool = typer.Option(
        False,
        "--merge",
        help="Apply overrides and run three-way merge (requires --data-home).",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit structured JSON to stdout."
    ),
) -> None:
    """Diff two collections or AnalysisBundles (FR-03-4 diff engine)."""
    from book2skill.application.diff import DiffEngine, load_diff_input
    from book2skill.domain.errors import DomainError
    from book2skill.storage.override_storage import OverrideStorage
    from book2skill.storage.schema_storage import KnowledgeSchemaStorage

    schema_storage = (
        KnowledgeSchemaStorage(data_home) if data_home is not None else None
    )

    try:
        old_units = load_diff_input(old, schema_storage=schema_storage)
        new_units = load_diff_input(new, schema_storage=schema_storage)
    except DomainError as exc:
        _print_diagnostic(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})",
            json_output=json_output,
        )
        if json_output:
            _write_json_error(
                code=exc.code.value, message=exc.message, recovery=exc.recovery
            )
        raise typer.Exit(code=1) from exc

    engine = DiffEngine()
    result = engine.diff(old_units, new_units)

    merge_payload: dict[str, int] | None = None
    if merge:
        if data_home is None:
            message = "--merge requires --data-home to load overrides."
            _print_diagnostic(f"[red]ERROR[/red] {message}", json_output=json_output)
            if json_output:
                _write_json_error(code="MERGE_DATA_HOME_REQUIRED", message=message)
            raise typer.Exit(code=1)
        # Overrides are keyed by collection; load from the *new* side when it
        # resolves to a collection_id, else from the old side.
        override_collection = new if not Path(new).is_file() else old
        if Path(override_collection).is_file():
            _print_diagnostic(
                "[yellow]WARN[/yellow] --merge needs a collection_id for "
                "override loading; skipping merge.",
                json_output=json_output,
            )
        else:
            overrides = OverrideStorage(data_home).load_active_overrides(
                override_collection
            )
            merge_result = engine.merge_with_overrides(
                old_units, new_units, overrides
            )
            merge_payload = {
                "merged_count": len(merge_result.merged),
                "applied_overrides": len(merge_result.applied_overrides),
                "preserved_overrides": len(merge_result.preserved_overrides),
                "new_conflicts": len(merge_result.new_conflicts),
                "unresolvable": len(merge_result.unresolvable),
            }
        # else: warning already printed, merge_payload stays None

    if json_output:
        payload = {
            "added": [u.unit_id for u in result.added],
            "removed": [u.unit_id for u in result.removed],
            "modified": [
                {
                    "unit_id": c.unit_id,
                    "changed_fields": c.changed_fields,
                }
                for c in result.modified
            ],
            "conflicts": len(result.conflicts),
            "unchanged": len(result.unchanged),
        }
        if merge_payload is not None:
            payload["merge"] = merge_payload
        _write_json(payload)
        return

    # Human-readable report.
    console.print("[bold]Diff result[/bold]")
    console.print(f"  added:      {len(result.added)}")
    for u in result.added:
        console.print(f"    + {u.unit_id} ({u.kind})")
    console.print(f"  removed:    {len(result.removed)}")
    for u in result.removed:
        console.print(f"    - {u.unit_id} ({u.kind})")
    console.print(f"  modified:   {len(result.modified)}")
    for c in result.modified:
        console.print(
            f"    ~ {c.unit_id}: {', '.join(c.changed_fields)}"
        )
    console.print(f"  conflicts:  {len(result.conflicts)}")
    for cf in result.conflicts:
        console.print(f"    ! {cf.conflict_id}: {cf.description}")
    console.print(f"  unchanged:  {len(result.unchanged)}")

    if merge_payload is not None:
        console.print("[bold]Merge result[/bold]")
        console.print(f"  merged units:         {merge_payload['merged_count']}")
        console.print(f"  applied overrides:    {merge_payload['applied_overrides']}")
        console.print(
            f"  preserved overrides:  {merge_payload['preserved_overrides']}"
        )
        console.print(f"  new conflicts:        {merge_payload['new_conflicts']}")
        console.print(f"  unresolvable:         {merge_payload['unresolvable']}")

    if not result.has_changes and merge_payload is None:
        console.print("[dim]No differences detected.[/dim]")


@app.command(name="validate")
def validate(
    skill_dir: Path = typer.Argument(
        ..., help="Skill directory to validate (produced by `book2skill build`)."
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the QualityReport as JSON to stdout.",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="Write quality-report.md and quality-report.json into the skill dir.",
    ),
    max_quote_words: int = typer.Option(
        25,
        "--max-quote-words",
        help="Soft cap on direct quotation length (PRD FR-08 default 25).",
    ),
) -> None:
    """Validate a Skill directory against quality and security gates (FR-07/FR-08)."""

    # Rebuild the check list so the copyright threshold is configurable.
    from book2skill.domain.errors import DomainError
    from book2skill.validation import (
        BudgetCheck,
        CopyrightCheck,
        FrontmatterCheck,
        InjectionCheck,
        QualityReportWriter,
        SourceCheck,
        Validator,
    )

    checks = [
        FrontmatterCheck(),
        SourceCheck(),
        CopyrightCheck(max_quote_words=max_quote_words),
        InjectionCheck(),
        BudgetCheck(),
    ]

    try:
        validator = Validator(skill_dir, checks=checks)
        report = validator.validate()
    except DomainError as exc:
        _print_diagnostic(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})",
            json_output=json_output,
        )
        if json_output:
            _write_json_error(
                code=exc.code.value, message=exc.message, recovery=exc.recovery
            )
        raise typer.Exit(code=1) from exc

    if write:
        writer = QualityReportWriter(skill_dir=Path(skill_dir))
        md_path, json_path = writer.write(report)
        if not json_output:
            console.print(f"[green]Wrote[/green] {md_path}")
            console.print(f"[green]Wrote[/green] {json_path}")

    if json_output:
        _write_json(report.model_dump(mode="json"))
    else:
        console.print(f"[bold]Quality report[/bold] — {skill_dir}")
        console.print(f"  run_id:  {report.run_id}")
        console.print(f"  status:  {report.status.value}")
        console.print("  checks:")
        for check in report.checks:
            check_id = str(check.get("check_id", ""))
            status = str(check.get("status", ""))
            message = str(check.get("message", ""))
            colour = {
                "pass": "green",
                "warn": "yellow",
                "fail": "red",
                "not_run": "dim",
            }.get(status, "white")
            console.print(
                f"    [{colour}]{status:<4}[/] {check_id}: {message}"
            )

    # Exit non-zero on failure so CI / scripts can act on it. Warnings are
    # acceptable per PRD FR-07 (``pass_with_warnings``).
    if report.status.value == "fail":
        raise typer.Exit(code=1)


@app.command(name="install")
def install(
    skill_dir: Path = typer.Argument(
        ..., help="Compiled Skill directory to install (output of `book2skill build`)."
    ),
    host: str = typer.Option(
        ...,
        "--host",
        "-h",
        help="Target host type: claude, trae, codex, project, or chatgpt.",
    ),
    project_level: bool = typer.Option(
        False,
        "--project-level",
        help=(
            "Install into the project's host directory (e.g. ./.claude/skills/) "
            "instead of the user-level home directory."
        ),
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Project root for project-level installs (default: current directory).",
    ),
    backup_root: Path | None = typer.Option(
        None,
        "--backup-root",
        help="Root directory for timestamped backups (default: ~/.book2skill/backups).",
    ),
    target_dir: str | None = typer.Option(
        None,
        "--target-dir",
        help="Subdirectory under project root for --host project (default: 'skills').",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview install actions without making any changes.",
    ),
    no_backup: bool = typer.Option(
        False,
        "--no-backup",
        help="Skip backing up the existing installation (the old tree is deleted).",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the InstallRecord as JSON to stdout."
    ),
) -> None:
    """Install a compiled Skill to a target host (FR-04 / FR-09)."""
    from book2skill.domain.errors import DomainError
    from book2skill.hosts import get_installer

    try:
        installer = get_installer(
            host,
            project_level=project_level,
            project_root=project_root,
            backup_root=backup_root,
            target_dir=target_dir,
        )
        record = installer.install(skill_dir, dry_run=dry_run, no_backup=no_backup)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except DomainError as exc:
        _print_diagnostic(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})",
            json_output=json_output,
        )
        if json_output:
            _write_json_error(
                code=exc.code.value, message=exc.message, recovery=exc.recovery
            )
        raise typer.Exit(code=1) from exc

    if json_output:
        payload = {
            "skill_name": record.skill_name,
            "target_dir": str(record.target_dir),
            "action": record.action,
            "backup_path": (
                str(record.backup_path) if record.backup_path else None
            ),
            "files_copied": record.files_copied,
            "dry_run": record.dry_run,
        }
        _write_json(payload)
        return

    if record.dry_run:
        console.print("[bold]Dry run — no changes made.[/bold]")
        for note in record.notes:
            console.print(f"  {note}")
        return

    console.print(
        f"[green]Installed[/green] {record.skill_name} → {record.target_dir} "
        f"({record.files_copied} files)"
    )
    if record.backup_path is not None:
        console.print(f"  backup: {record.backup_path}")


@app.command(name="uninstall")
def uninstall(
    skill_name: str = typer.Argument(
        ...,
        help=(
            "Skill name (slug) to uninstall, as it appears in SKILL.md frontmatter."
        ),
    ),
    host: str = typer.Option(
        ...,
        "--host",
        "-h",
        help="Target host type: claude, trae, codex, project, or chatgpt.",
    ),
    project_level: bool = typer.Option(
        False,
        "--project-level",
        help="Uninstall from the project's host directory instead of user-level home.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Project root for project-level uninstall (default: current directory).",
    ),
    backup_root: Path | None = typer.Option(
        None,
        "--backup-root",
        help="Root directory for timestamped backups (default: ~/.book2skill/backups).",
    ),
    target_dir: str | None = typer.Option(
        None,
        "--target-dir",
        help="Subdirectory under project root for --host project (default: 'skills').",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview uninstall actions without making any changes.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the InstallRecord as JSON to stdout."
    ),
) -> None:
    """Uninstall a Skill from a target host (FR-04 / FR-09)."""
    from book2skill.domain.errors import DomainError
    from book2skill.hosts import get_installer

    try:
        installer = get_installer(
            host,
            project_level=project_level,
            project_root=project_root,
            backup_root=backup_root,
            target_dir=target_dir,
        )
        record = installer.uninstall(skill_name, dry_run=dry_run)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except DomainError as exc:
        _print_diagnostic(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})",
            json_output=json_output,
        )
        if json_output:
            _write_json_error(
                code=exc.code.value, message=exc.message, recovery=exc.recovery
            )
        raise typer.Exit(code=1) from exc

    if json_output:
        payload = {
            "skill_name": record.skill_name,
            "target_dir": str(record.target_dir),
            "action": record.action,
            "backup_path": (
                str(record.backup_path) if record.backup_path else None
            ),
            "files_copied": record.files_copied,
            "dry_run": record.dry_run,
        }
        _write_json(payload)
        return

    if record.dry_run:
        console.print("[bold]Dry run — no changes made.[/bold]")
        for note in record.notes:
            console.print(f"  {note}")
        return

    console.print(
        f"[green]Uninstalled[/green] {record.skill_name} ← {record.target_dir}"
    )


if __name__ == "__main__":
    app()
