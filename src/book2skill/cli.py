"""Command-line interface for Book2Skill."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

from book2skill import __version__
from book2skill.application.analyze import AnalyzeUseCase
from book2skill.application.batch import BatchOrchestrator
from book2skill.application.build import BuildUseCase
from book2skill.application.diff import DiffEngine, load_diff_input
from book2skill.application.update import UpdateUseCase
from book2skill.compiler import SkillSpec
from book2skill.domain.errors import DomainError
from book2skill.hosts import HOST_KINDS, get_installer
from book2skill.llm.ports import LLMAdapter
from book2skill.storage.override_storage import OverrideStorage
from book2skill.storage.schema_storage import KnowledgeSchemaStorage
from book2skill.validation import QualityReportWriter, Validator

app = typer.Typer(name="book2skill", help="Book2Skill CLI")
console = Console()


def _build_llm_adapter(
    kind: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> LLMAdapter:
    """Build an LLM adapter from CLI flags.

    Returns a MockLLMAdapter (default) or an OpenAIAdapter. The OpenAIAdapter
    gracefully falls back to the mock when the ``openai`` package is missing
    or no API key is set.
    """
    if kind == "mock":
        from book2skill.llm.mock_adapter import MockLLMAdapter

        return MockLLMAdapter()
    if kind == "openai":
        from book2skill.llm.openai_adapter import OpenAIAdapter

        return OpenAIAdapter(model=model or "gpt-4o", base_url=base_url)
    raise typer.BadParameter(
        f"Unknown LLM adapter '{kind}'. Expected 'mock' or 'openai'."
    )


@app.command(name="hello")
def hello() -> None:
    """Placeholder hello command for M0."""
    console.print("book2skill v0.1.0 — ready for M0")


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
    data_home: Path | None = typer.Option(
        None,
        "--data-home",
        help="Directory for raw storage (default: in-memory).",
    ),
    llm: str = typer.Option(
        "mock",
        "--llm",
        help="LLM adapter: 'mock' (offline, default) or 'openai'.",
    ),
    llm_model: str | None = typer.Option(
        None,
        "--llm-model",
        help="Model name for the openai adapter (e.g. gpt-4o).",
    ),
    llm_base_url: str | None = typer.Option(
        None,
        "--llm-base-url",
        help="Base URL for OpenAI-compatible endpoint (e.g. http://localhost:11434/v1).",
    ),
) -> None:
    """Analyze sources without generating a final Skill (FR-03-1)."""
    adapter = _build_llm_adapter(llm, model=llm_model, base_url=llm_base_url)
    use_case = AnalyzeUseCase(data_home=data_home, llm=adapter)
    result = use_case.execute(
        [str(s) for s in sources],
        collection_id=collection_id,
        rights_note=rights_note,
    )

    if result.errors:
        for err in result.errors:
            console.print(
                f"[red]ERROR[/red] {err.code.value}: {err.message} "
                f"(recovery: {err.recovery})"
            )

    if result.bundle is None:
        console.print("[red]No valid sources to analyze.[/red]")
        raise typer.Exit(code=1)

    if json_output:
        import json

        bundle_json = json.dumps(
            result.bundle.model_dump(mode="json"), indent=2, ensure_ascii=False
        )
        sys.stdout.write(bundle_json + "\n")
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
    data_home: Path | None = typer.Option(
        None,
        "--data-home",
        help="Directory for raw storage (default: in-memory).",
    ),
    llm: str = typer.Option(
        "mock",
        "--llm",
        help="LLM adapter: 'mock' (offline, default) or 'openai'.",
    ),
    llm_model: str | None = typer.Option(
        None,
        "--llm-model",
        help="Model name for the openai adapter.",
    ),
    llm_base_url: str | None = typer.Option(
        None,
        "--llm-base-url",
        help="Base URL for OpenAI-compatible endpoint.",
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
    adapter = _build_llm_adapter(llm, model=llm_model, base_url=llm_base_url)
    use_case = AnalyzeUseCase(data_home=data_home, llm=adapter)
    orchestrator = BatchOrchestrator(use_case=use_case)

    def _on_progress(index: int, total: int, path: str) -> None:
        if not json_output:
            console.print(
                f"[dim]({index}/{total})[/dim] Processing {Path(path).name} ..."
            )

    result = orchestrator.execute(
        [str(s) for s in sources],
        rights_note=rights_note,
        on_progress=_on_progress,
        checkpoint_path=checkpoint,
        resume=resume,
    )

    if json_output:
        import json

        batch_json = json.dumps(
            result.model_dump(mode="json"), indent=2, ensure_ascii=False
        )
        sys.stdout.write(batch_json + "\n")
    else:
        s = result.summary
        console.print(
            f"[green]Batch complete[/green] — "
            f"total: {s.total}, succeeded: {s.succeeded}, "
            f"partial: {s.partial}, failed: {s.failed}, skipped: {s.skipped}"
        )
        for o in result.outcomes:
            if o.status == "success" and o.bundle is not None:
                console.print(
                    f"  [green]OK[/green]   {o.path} ({o.source_id})"
                )
            elif o.status == "partial":
                console.print(
                    f"  [yellow]PART[/yellow] {o.path} ({o.source_id})"
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
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Output directory for the Skill (default: workspace/skills/<name>).",
    ),
    data_home: Path | None = typer.Option(
        None,
        "--data-home",
        help="Directory for raw/schema storage (enables provenance enrichment).",
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
) -> None:
    """Full Build or Build from Analysis (FR-03-2 / FR-03-3)."""
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
        )
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid SkillSpec: {exc}") from exc

    use_case = BuildUseCase(data_home=data_home)

    try:
        if from_analysis is not None:
            result = use_case.build_from_bundle(
                from_analysis, spec, output_dir=output_dir
            )
        else:
            assert sources is not None  # narrowed by the guard above
            result = use_case.build_from_sources(
                [str(s) for s in sources],
                spec,
                rights_note=rights_note,
                output_dir=output_dir,
            )
    except DomainError as exc:
        console.print(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})"
        )
        raise typer.Exit(code=1) from exc

    if result.errors:
        for err in result.errors:
            console.print(
                f"[yellow]WARN[/yellow] {err.code.value}: {err.message} "
                f"(recovery: {err.recovery})"
            )

    if result.skill_dir is None:
        console.print("[red]Build failed: no valid sources analysed.[/red]")
        raise typer.Exit(code=1)

    if json_output:
        import json

        payload = {
            "skill_dir": str(result.skill_dir),
            "collection_id": result.collection_id,
            "source_count": len(result.source_manifests),
            "warnings": len(result.errors),
        }
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
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
        help="Atomically publish the folded-in Skill (default: dry-run plan).",
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
) -> None:
    """Update / Fold-in a published Skill (FR-03-4)."""
    if rollback:
        if new_sources:
            raise typer.BadParameter("SOURCES cannot be combined with --rollback.")
        use_case = UpdateUseCase(data_home)
        try:
            record = use_case.rollback(skill_dir)
        except DomainError as exc:
            console.print(
                f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
                f"(recovery: {exc.recovery})"
            )
            raise typer.Exit(code=1) from exc

        if json_output:
            import json

            rb_payload: dict[str, object] = {
                "action": record.action,
                "skill_dir": str(record.skill_dir),
                "snapshot_path": (
                    str(record.snapshot_path) if record.snapshot_path else None
                ),
                "published_at": record.published_at,
            }
            sys.stdout.write(
                json.dumps(rb_payload, indent=2, ensure_ascii=False) + "\n"
            )
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

    use_case = UpdateUseCase(data_home)
    try:
        result = use_case.execute(
            skill_dir,
            [str(s) for s in new_sources],
            spec,
            collection_id=collection_id,
            rights_note=rights_note,
            confirm=confirm,
        )
    except DomainError as exc:
        console.print(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})"
        )
        raise typer.Exit(code=1) from exc

    s = result.suggestion
    if json_output:
        import json

        payload: dict[str, object] = {
            "published": result.published,
            "reason": result.reason,
            "collection_id": result.collection_id,
            "added": len(s.added),
            "modified": len(s.modified),
            "deprecated": len(s.removed),
            "conflicts": len(s.conflicts),
            "new_conflicts": len(s.merge.new_conflicts) if s.merge else 0,
            "applied_overrides": len(s.merge.applied_overrides) if s.merge else 0,
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
        sys.stdout.write(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        )
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
    schema_storage = (
        KnowledgeSchemaStorage(data_home) if data_home is not None else None
    )

    try:
        old_units = load_diff_input(old, schema_storage=schema_storage)
        new_units = load_diff_input(new, schema_storage=schema_storage)
    except DomainError as exc:
        console.print(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})"
        )
        raise typer.Exit(code=1) from exc

    engine = DiffEngine()
    result = engine.diff(old_units, new_units)

    merge_payload: dict[str, int] | None = None
    if merge:
        if data_home is None:
            console.print(
                "[red]ERROR[/red] --merge requires --data-home to load overrides."
            )
            raise typer.Exit(code=1)
        # Overrides are keyed by collection; load from the *new* side when it
        # resolves to a collection_id, else from the old side.
        override_collection = new if not Path(new).is_file() else old
        if Path(override_collection).is_file():
            console.print(
                "[yellow]WARN[/yellow] --merge needs a collection_id for "
                "override loading; skipping merge."
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
        import json

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
        sys.stdout.write(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        )
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
    from book2skill.validation import (
        BudgetCheck,
        CopyrightCheck,
        FrontmatterCheck,
        InjectionCheck,
        SourceCheck,
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
        console.print(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})"
        )
        raise typer.Exit(code=1) from exc

    if write:
        writer = QualityReportWriter(skill_dir=Path(skill_dir))
        md_path, json_path = writer.write(report)
        if not json_output:
            console.print(f"[green]Wrote[/green] {md_path}")
            console.print(f"[green]Wrote[/green] {json_path}")

    if json_output:
        import json

        sys.stdout.write(
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False)
            + "\n"
        )
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
        help=f"Target host type. One of: {', '.join(HOST_KINDS)}.",
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
        console.print(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})"
        )
        raise typer.Exit(code=1) from exc

    if json_output:
        import json

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
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
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
        help=f"Target host type. One of: {', '.join(HOST_KINDS)}.",
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
        console.print(
            f"[red]ERROR[/red] {exc.code.value}: {exc.message} "
            f"(recovery: {exc.recovery})"
        )
        raise typer.Exit(code=1) from exc

    if json_output:
        import json

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
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
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
