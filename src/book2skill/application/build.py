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
  with ``data_home``). Build from Analysis has no manifests, so its
  provenance falls back to ``unknown`` metadata.
- The use case does NOT publish or install the produced Skill; that is the
  responsibility of M4 (publisher) and M5 (hosts).
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from book2skill.application.analyze import AnalyzeUseCase
from book2skill.application.candidates import candidate_to_unit
from book2skill.application.gate import GateError
from book2skill.application.models import AnalysisBundle
from book2skill.application.progress import (
    STAGE_COMPILE,
    ProgressReporter,
    noop_progress,
)
from book2skill.application.publisher import SkillMeta
from book2skill.compiler import IRBuilder, SkillIR, SkillSpec, SkillWriter
from book2skill.compiler.ir_builder import WorkflowStep
from book2skill.compiler.wiki_generator import WikiGenerator
from book2skill.domain import (
    DomainError,
    ErrorCode,
    KnowledgeUnit,
    PublishStatus,
    SourceManifest,
)
from book2skill.storage import FileRawStorage, RawStorage, atomic_write
from book2skill.storage.schema_storage import KnowledgeSchemaStorage

#: Default output root when the caller does not pass ``output_dir``. Relative
#: to the process cwd; CLI overrides this with ``--output-dir``.
_DEFAULT_OUTPUT_ROOT = Path("workspace") / "skills"

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


class BuildUseCase:
    """Orchestrate Full Build and Build from Analysis.

    Dependencies are injected so tests can substitute fakes. The defaults
    construct a fresh :class:`AnalyzeUseCase` and a
    :class:`KnowledgeSchemaStorage` rooted at *data_home*; without
    *data_home* the Analyze stage runs in-memory and Schema persistence is
    skipped (only the Skill directory is produced, with stub provenance).
    """

    def __init__(
        self,
        *,
        analyze_use_case: AnalyzeUseCase | None = None,
        schema_storage: KnowledgeSchemaStorage | None = None,
        raw_storage: RawStorage | None = None,
        writer: SkillWriter | None = None,
        data_home: Path | None = None,
    ) -> None:
        self._data_home = data_home.resolve() if data_home else None
        self._analyze = analyze_use_case or AnalyzeUseCase(data_home=data_home)
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
                ``workspace/skills/<spec.name>`` under the cwd.
            on_progress: Optional stage-progress callback (UI-neutral),
                forwarded to the Analyze stage and used for the compile tail.

        Returns:
            A :class:`BuildResult` with ``skill_dir`` set on success.
        """
        reporter = on_progress or noop_progress
        analyze_result = self._analyze.execute(
            inputs,
            collection_id=collection_id,
            rights_note=rights_note,
            on_progress=reporter,
        )
        if analyze_result.bundle is None:
            return BuildResult(errors=analyze_result.errors)

        bundle = analyze_result.bundle
        manifests = self._collect_manifests(bundle.source_ids)

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
                candidate units / no source_ids.
        """
        reporter = on_progress or noop_progress
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

        # Build from Analysis has no RawStorage handle; manifests stay empty
        # and provenance falls back to ``unknown`` metadata.
        reporter(STAGE_COMPILE, 0, 1, spec.name)
        result = self._compile_bundle(
            bundle=bundle,
            spec=spec,
            output_dir=output_dir,
            source_manifests=[],
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
        units = self._persist_units(bundle)
        builder = IRBuilder(units, spec)
        ir = builder.build()
        references = builder.build_references()
        wiki_files = WikiGenerator(units, skill_name=spec.name).build()

        target_dir = output_dir or (Path.cwd() / _DEFAULT_OUTPUT_ROOT / spec.name)
        # Honour an injected writer only when the caller did not pass an
        # explicit output_dir; explicit output_dir always wins so the CLI
        # ``--output-dir`` flag is deterministic.
        writer = (
            SkillWriter(output_dir=output_dir)
            if output_dir is not None
            else self._writer or SkillWriter(output_dir=target_dir)
        )

        skill_dir = writer.write(
            ir,
            references=references,
            source_manifests=source_manifests or None,
            wiki_files=wiki_files,
        )

        # Persist per-skill metadata so Update (TASK-015) can reload the
        # authored shape and locate the Schema collection without re-asking.
        self._write_skill_meta(skill_dir, spec, bundle.collection_id)

        return BuildResult(
            skill_dir=skill_dir,
            bundle=bundle,
            collection_id=bundle.collection_id,
            source_manifests=source_manifests,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _persist_units(self, bundle: AnalysisBundle) -> list[KnowledgeUnit]:
        """Convert candidates to KnowledgeUnits and persist them.

        When Schema storage is configured (``data_home`` provided), units are
        appended to ``units.jsonl`` so the build is auditable. Without
        storage the units are returned in-memory only.
        """
        units = [candidate_to_unit(c) for c in bundle.candidate_units]
        if self._schema_storage is not None:
            for u in units:
                self._schema_storage.save_unit(bundle.collection_id, u)
        return units

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
