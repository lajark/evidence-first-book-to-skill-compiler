"""Build use cases (PRD FR-03-2 Full Build, FR-03-3 Build from Analysis).

Two entry points share the same compile tail:

- :meth:`BuildUseCase.build_from_sources` — Full Build: discover → gate →
  extract → analyze (via :class:`~book2skill.application.analyze.AnalyzeUseCase`)
  → normalize to :class:`~book2skill.domain.KnowledgeUnit` → compile to a
  Skill directory. The Analyze stage is reused verbatim so the build path
  inherits all gate/extractor/LLM behaviour without duplication.

- :meth:`BuildUseCase.build_from_bundle` — Build from Analysis: load a
  previously produced :class:`~book2skill.application.models.AnalysisBundle`
  JSON file (typically reviewed/edited by a human), skip extraction, and
  run the same normalize → compile tail.

The compile tail persists knowledge units to the Schema layer
(``units.jsonl``) so the build is auditable, then invokes
:class:`~book2skill.compiler.IRBuilder` and
:class:`~book2skill.compiler.SkillWriter` to produce the standard Skill
directory.

Design notes
------------

- The use case stays online-offline neutral: it inherits whatever
  :class:`LLMAdapter` the wrapped :class:`AnalyzeUseCase` uses (Mock by
  default, real LLM when injected). M3 keeps the Mock adapter per N-02.
- ``provenance.yml`` is enriched with real ``content_sha256`` /
  ``original_name`` from :class:`~book2skill.domain.SourceManifest` records
  when the wrapped Analyze pipeline persisted them to RawStorage (Full Build
  with ``data_home``). Build from Analysis is an external trust boundary and
  therefore requires the matching RawStorage; unverified bundles fail closed.
- The use case does NOT publish or install the produced Skill; that is the
  responsibility of M4 (publisher) and M5 (hosts).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from book2skill.application.analyze import AnalyzeUseCase
from book2skill.application.artifacts import (
    CompilationArtifact,
    verify_compilation_artifact,
    write_compilation_artifact,
)
from book2skill.application.bundle_trace import verify_bundle_against_raw
from book2skill.application.gate import GateError
from book2skill.application.models import AnalysisBundle
from book2skill.application.normalized_bundle import (
    NormalizedBundle,
    normalize_analysis_bundle,
    write_normalized_bundle,
)
from book2skill.application.progress import (
    BUILD_STAGE_ORDER,
    STAGE_COMPILE,
    ProgressReporter,
    noop_progress,
    weighted_progress,
)
from book2skill.application.publisher import SkillMeta
from book2skill.application.skill_design import (
    generate_skill_fixtures,
    review_skill_design,
    write_skill_design_artifacts,
)
from book2skill.compiler import IRBuilder, SkillIR, SkillSpec, SkillWriter
from book2skill.compiler.ir_builder import WorkflowStep
from book2skill.compiler.wiki_generator import WikiGenerator
from book2skill.domain import (
    DomainError,
    ErrorCode,
    PublishStatus,
    SourceManifest,
)
from book2skill.llm.ports import LLMAdapter
from book2skill.llm.runtime import LLMRuntimeConfig
from book2skill.storage import FileRawStorage, RawStorage, atomic_write
from book2skill.storage.schema_storage import KnowledgeSchemaStorage
from book2skill.validation import (
    QualityReportWriter,
    ValidationProfile,
    Validator,
    build_compatibility_report,
    evaluate_publication_quality,
    write_compatibility_report,
)

#: Default output root when the caller does not pass ``output_dir``. Relative
#: to the process cwd; CLI overrides this with ``--output-dir``.
_DEFAULT_OUTPUT_ROOT = Path("output") / "skills"

#: Source version loaded for provenance enrichment. The Analyze pipeline
#: currently persists version 1; if multi-version raw storage lands later
#: this constant will be replaced with a lookup over ``list_versions``.
_DEFAULT_SOURCE_VERSION = 1


@dataclass
class BuildResult:
    """Outcome of a Build run.

    On success *skill_dir* points at the generated Skill directory and
    *bundle* carries the :class:`AnalysisBundle` used. *errors* collects
    gate/discovery errors from the Analyze stage (Full Build only); they do
    not prevent a build when at least one source survived.
    """

    skill_dir: Path | None = None
    bundle: AnalysisBundle | None = None
    collection_id: str | None = None
    source_manifests: list[SourceManifest] = field(default_factory=list)
    errors: list[GateError] = field(default_factory=list)
    artifact: CompilationArtifact | None = None
    normalized_bundle: NormalizedBundle | None = None
    publication_ready: bool = False


class BuildUseCase:
    """Orchestrate Full Build and Build from Analysis.

    Dependencies are injected so tests can substitute fakes. The defaults
    construct a fresh :class:`AnalyzeUseCase` and a
    :class:`KnowledgeSchemaStorage` rooted at *data_home*; without
    *data_home* the Analyze stage runs in-memory and Schema persistence is
    skipped for Full Build. Build from Analysis requires RawStorage so its
    externally supplied references can be verified before persistence.
    """

    def __init__(
        self,
        *,
        analyze_use_case: AnalyzeUseCase | None = None,
        schema_storage: KnowledgeSchemaStorage | None = None,
        raw_storage: RawStorage | None = None,
        writer: SkillWriter | None = None,
        data_home: Path | None = None,
        runtime_config: LLMRuntimeConfig | None = None,
        llm: LLMAdapter | None = None,
    ) -> None:
        self._data_home = data_home.resolve() if data_home else None
        # Pass the full adapter (e.g. ``RouterLLMAdapter`` for ``balanced``)
        # so the analyze layer uses multi-channel routing; ``runtime_config``
        # is the single-channel fallback. Mutually exclusive.
        self._analyze = analyze_use_case or AnalyzeUseCase(
            data_home=data_home, runtime_config=runtime_config, llm=llm
        )
        self._schema_storage = schema_storage or (
            KnowledgeSchemaStorage(self._data_home) if self._data_home else None
        )
        self._raw_storage = raw_storage or (
            FileRawStorage(self._data_home) if self._data_home else None
        )
        self._writer = writer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_from_sources(
        self,
        inputs: list[str],
        spec: SkillSpec,
        *,
        collection_id: str | None = None,
        rights_note: str | None = None,
        output_dir: Path | None = None,
        on_progress: ProgressReporter | None = None,
    ) -> BuildResult:
        """Full Build: sources → analyze → schema → ir → skill directory.

        Args:
            inputs: File paths, directories or glob patterns (forwarded to
                :class:`AnalyzeUseCase`).
            spec: Human-authored Skill shape (name/description/use_when/...).
            collection_id: Optional collection identifier; auto-derived when
                omitted.
            rights_note: Optional rights-confirmation note forwarded to the
                Analyze stage.
            output_dir: Where to write the Skill directory. Defaults to
                ``output/skills/<spec.name>`` under the cwd.
            on_progress: Optional stage-progress callback (UI-neutral),
                forwarded to the Analyze stage and used for the compile tail.

        Returns:
            A :class:`BuildResult` with ``skill_dir`` set on success.
        """
        reporter = weighted_progress(
            on_progress or noop_progress,
            stages=BUILD_STAGE_ORDER,
        )
        analyze_result = self._analyze.execute(
            inputs,
            collection_id=collection_id,
            rights_note=rights_note,
            on_progress=reporter,
        )
        if analyze_result.bundle is None:
            return BuildResult(errors=analyze_result.errors)

        bundle = analyze_result.bundle
        # Full Build is not exempt from the artifact trust boundary: an
        # adapter result can still contain an invented source or block ID.
        # When no data_home is used, reuse Analyze's in-memory Raw store
        # instead of verifying against a new empty store.
        verified_manifests = verify_bundle_against_raw(
            bundle,
            self._raw_storage or self._analyze.raw_storage,
            source_version=_DEFAULT_SOURCE_VERSION,
            input_id=bundle.collection_id,
        )
        # In-memory Full Build still verifies the live Analyze result, but it
        # cannot produce a replayable publication artifact after this process
        # exits. Keep that convenience path as an explicitly non-publishable
        # draft by omitting transient manifests from the rendered ledger.
        manifests = verified_manifests if self._data_home is not None else []

        reporter(STAGE_COMPILE, 0, 1, spec.name)
        result = self._compile_bundle(
            bundle=bundle,
            spec=spec,
            output_dir=output_dir,
            source_manifests=manifests,
            errors=analyze_result.errors,
        )
        reporter(STAGE_COMPILE, 1, 1, spec.name)
        return result

    def build_from_bundle(
        self,
        bundle_path: Path,
        spec: SkillSpec,
        *,
        output_dir: Path | None = None,
        on_progress: ProgressReporter | None = None,
    ) -> BuildResult:
        """Build from Analysis: load bundle.json → compile tail.

        Args:
            bundle_path: Path to an :class:`AnalysisBundle` JSON file (as
                emitted by ``book2skill analyze --json``). The file may have
                been hand-edited between Analyze and Build to review/correct
                candidate units.
            spec: Human-authored Skill shape.
            output_dir: Where to write the Skill directory.
            on_progress: Optional stage-progress callback (UI-neutral), used
                for the compile tail.

        Raises:
            DomainError: With :data:`ErrorCode.BUILD_INPUT_INVALID` when the
                file is missing, not valid JSON, or the bundle has no
                candidate units / no source_ids; or with
                :data:`ErrorCode.BUILD_SOURCE_TRACE_INVALID` when its source
                references cannot be verified against matching Raw records.
        """
        reporter = weighted_progress(
            on_progress or noop_progress,
            stages=(STAGE_COMPILE,),
        )
        if not bundle_path.exists():
            raise DomainError(
                code=ErrorCode.BUILD_INPUT_INVALID,
                input_id=str(bundle_path),
                message=f"AnalysisBundle file not found: {bundle_path}",
                recovery=(
                    "Run `book2skill analyze --json` first and pass its "
                    "output path."
                ),
            )

        try:
            raw = bundle_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            bundle = AnalysisBundle.model_validate(data)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise DomainError(
                code=ErrorCode.BUILD_INPUT_INVALID,
                input_id=str(bundle_path),
                message=f"Invalid AnalysisBundle JSON: {exc}",
                recovery=(
                    "Regenerate the bundle with `book2skill analyze --json` "
                    "and avoid hand-editing structural fields."
                ),
            ) from exc

        if not bundle.candidate_units:
            raise DomainError(
                code=ErrorCode.BUILD_INPUT_INVALID,
                input_id=str(bundle_path),
                message="AnalysisBundle has no candidate_units; nothing to compile.",
                recovery=(
                    "Re-run analyze on a richer source or add candidate units "
                    "manually."
                ),
            )

        manifests = verify_bundle_against_raw(
            bundle,
            self._raw_storage,
            source_version=_DEFAULT_SOURCE_VERSION,
            input_id=str(bundle_path),
        )

        reporter(STAGE_COMPILE, 0, 1, spec.name)
        result = self._compile_bundle(
            bundle=bundle,
            spec=spec,
            output_dir=output_dir,
            source_manifests=manifests,
            errors=[],
        )
        reporter(STAGE_COMPILE, 1, 1, spec.name)
        return result

    # ------------------------------------------------------------------
    # Compile tail (shared by both entry points)
    # ------------------------------------------------------------------

    def _compile_bundle(
        self,
        *,
        bundle: AnalysisBundle,
        spec: SkillSpec,
        output_dir: Path | None,
        source_manifests: list[SourceManifest],
        errors: list[GateError],
    ) -> BuildResult:
        """Persist units, build IR, write Skill directory."""
        normalization = normalize_analysis_bundle(bundle)
        units = normalization.units
        builder = IRBuilder(units, spec)
        ir = builder.build()
        references = builder.build_references()
        wiki_files = WikiGenerator(units, skill_name=spec.name).build()

        target_dir = (
            output_dir or (Path.cwd() / _DEFAULT_OUTPUT_ROOT / spec.name)
        ).resolve()
        # An injected writer is retained as a hermetic test seam. Normal
        # production builds compile into a sibling staging directory and only
        # expose it after validation and metadata writing have succeeded.
        direct_writer = output_dir is None and self._writer is not None
        staging_dir: Path | None = None
        backup_dir: Path | None = None
        swapped = False
        if direct_writer:
            writer = self._writer
            assert writer is not None
        else:
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            staging_dir = target_dir.parent / (
                f".{target_dir.name}.staging-{uuid.uuid4().hex}"
            )
            writer = SkillWriter(output_dir=staging_dir)

        try:
            skill_dir = writer.write(
                ir,
                references=references,
                source_manifests=source_manifests or None,
                wiki_files=wiki_files,
            )
            write_normalized_bundle(skill_dir, normalization.bundle)
            write_skill_design_artifacts(
                skill_dir,
                review_skill_design(bundle, spec, ir),
                generate_skill_fixtures(bundle, spec, ir),
            )
        except Exception:
            if staging_dir is not None and staging_dir.exists():
                self._remove_tree(staging_dir)
            raise

        try:
            # Replace SkillWriter's placeholder with the real report for every
            # Build, including drafts. A draft may legitimately be below the
            # publication bar, but the report must expose that fact to
            # reviewers.
            report = Validator(skill_dir).validate()
            if staging_dir is not None:
                report = report.model_copy(
                    update={"run_id": f"{target_dir.name}-{report.run_id}"}
                )
            QualityReportWriter(skill_dir).write(report)
            compatibility = build_compatibility_report(
                skill_dir,
                report,
                profile=ValidationProfile.PORTABLE_DRAFT,
            )
            write_compatibility_report(skill_dir, compatibility)
            publication_ready = evaluate_publication_quality(report).publishable

            # Persist per-skill metadata before exposing a staged tree.
            self._write_skill_meta(skill_dir, spec, bundle.collection_id)
            if bundle.analysis_run is not None:
                atomic_write(
                    skill_dir / "analysis-run.json",
                    bundle.analysis_run.model_dump_json(indent=2),
                )
            artifact = write_compilation_artifact(
                skill_dir,
                collection_id=bundle.collection_id,
                spec=spec,
                units=units,
                source_manifests=source_manifests,
            )
            if not verify_compilation_artifact(skill_dir, artifact, units=units):
                raise DomainError(
                    code=ErrorCode.SCHEMA_VALIDATION_FAILED,
                    input_id=bundle.collection_id,
                    message="The staged compilation artifact is inconsistent.",
                    recovery="Rebuild the Skill and retry.",
                )

            if staging_dir is not None:
                backup_dir = self._swap_draft_tree(staging_dir, target_dir)
                skill_dir = target_dir
                swapped = True

            # Schema history is committed only after compilation, validation
            # and metadata have succeeded. Unchanged rebuilds do not append
            # duplicate records to the append-only history.
            if self._schema_storage is not None:
                self._schema_storage.save_new_units_atomic(
                    bundle.collection_id, units
                )
        except Exception as exc:
            if swapped:
                restored = (
                    self._restore_draft_tree(target_dir, backup_dir)
                    if backup_dir is not None
                    else self._remove_new_draft(target_dir)
                )
                if not restored:
                    raise DomainError(
                        code=ErrorCode.PUBLISH_ROLLBACK_FAILED,
                        input_id=str(target_dir),
                        message=(
                            "Build failed and the previous draft could not "
                            "be restored."
                        ),
                        recovery=(
                            "Inspect the staging/backup directories before "
                            "retrying the build."
                        ),
                    ) from exc
                raise DomainError(
                    code=ErrorCode.SCHEMA_VALIDATION_FAILED,
                    input_id=bundle.collection_id,
                    message=(
                        "Build Schema commit failed; the previous draft "
                        "was restored."
                    ),
                    recovery="Resolve the Schema storage error and retry the build.",
                    details={"storage_error": type(exc).__name__},
                ) from exc
            raise
        finally:
            if staging_dir is not None and staging_dir.exists():
                self._remove_tree(staging_dir)
        if backup_dir is not None and backup_dir.exists():
            self._remove_tree(backup_dir)

        return BuildResult(
            skill_dir=skill_dir,
            bundle=bundle,
            collection_id=bundle.collection_id,
            source_manifests=source_manifests,
            errors=errors,
            artifact=artifact,
            normalized_bundle=normalization.bundle,
            publication_ready=publication_ready,
        )

    @staticmethod
    def _swap_draft_tree(staging_dir: Path, target_dir: Path) -> Path | None:
        """Atomically replace a draft directory and return its backup."""
        backup_dir: Path | None = None
        if target_dir.exists():
            backup_dir = target_dir.parent / (
                f".{target_dir.name}.previous-{uuid.uuid4().hex}"
            )
            os.replace(target_dir, backup_dir)
        try:
            os.replace(staging_dir, target_dir)
        except Exception:
            if backup_dir is not None and backup_dir.exists():
                os.replace(backup_dir, target_dir)
            raise
        return backup_dir

    @classmethod
    def _restore_draft_tree(
        cls, target_dir: Path, backup_dir: Path
    ) -> bool:
        """Restore a prior draft after a post-swap failure."""
        try:
            if target_dir.exists():
                cls._remove_tree(target_dir)
            os.replace(backup_dir, target_dir)
        except OSError:
            return False
        return target_dir.exists() and not backup_dir.exists()

    @classmethod
    def _remove_new_draft(cls, target_dir: Path) -> bool:
        """Remove a first-build tree after a post-swap commit failure."""
        try:
            if target_dir.exists():
                cls._remove_tree(target_dir)
        except OSError:
            return False
        return not target_dir.exists()

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_skill_meta(
        self, skill_dir: Path, spec: SkillSpec, collection_id: str
    ) -> None:
        """Persist ``skill_dir/skill.meta.json``.

        Carries the authored :class:`SkillSpec` and ``collection_id`` so the
        Update use case (TASK-015) can reload the shape and locate the old
        Schema collection without re-asking the caller. A build is a draft;
        the Publisher flips ``publish_status`` to ``published`` on confirm.
        """
        meta = SkillMeta(
            skill_name=spec.name,
            collection_id=collection_id,
            spec=spec,
            publish_status=PublishStatus.DRAFT,
            built_at=_dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            last_published_at=None,
        )
        atomic_write(
            skill_dir / "skill.meta.json", meta.model_dump_json(indent=2)
        )

    def _collect_manifests(self, source_ids: list[str]) -> list[SourceManifest]:
        """Load persisted :class:`SourceManifest` records for *source_ids*.

        Returns an empty list when RawStorage is unavailable (no *data_home*)
        or a manifest is missing — the caller falls back to ``unknown``
        provenance in that case.
        """
        if self._raw_storage is None:
            return []
        manifests: list[SourceManifest] = []
        for sid in source_ids:
            try:
                m = self._raw_storage.load_manifest(sid, _DEFAULT_SOURCE_VERSION)
            except Exception:
                # Missing manifest is non-fatal; provenance falls back to unknown.
                continue
            manifests.append(m)
        return manifests


# Re-export compiler types so callers can do
# ``from book2skill.application.build import BuildUseCase, SkillSpec``.
__all__ = [
    "BuildResult",
    "BuildUseCase",
    "SkillIR",
    "SkillSpec",
    "SkillWriter",
    "WorkflowStep",
]
