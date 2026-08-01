"""Public service facades for the Book2Skill SDK.

These are thin, stable adapters over Core internals so downstream extensions
can discover sources, extract text, persist data, compile Skills and run
validators without touching Core private modules. Extensions must not import
anything outside :mod:`book2skill.sdk`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from book2skill.application.gate import DiscoveredFile, Gate, GateError
from book2skill.compiler import ir_builder
from book2skill.compiler.ir_builder import IRBuilder, SkillIR, SkillSpec
from book2skill.compiler.skill_writer import SkillWriter
from book2skill.domain import (
    ExtractionMapEntry,
    KnowledgeUnit,
    SourceFormat,
    SourceManifest,
    TextBlock,
)
from book2skill.extractors.registry import ExtractorRegistry, default_registry
from book2skill.storage.file_storage import (
    FileRawStorage,
    FileSchemaStorage,
    FileWikiStorage,
)
from book2skill.storage.ports import RawStorage, SchemaStorage, WikiStorage
from book2skill.validation import QualityReport, Validator

__all__ = [
    "SourceService",
    "ExtractionService",
    "StorageService",
    "SkillCompilerService",
    "ValidatorRegistryService",
]


class SourceService:
    """Discover inputs, run the legality gate, and compute stable IDs.

    Wraps :class:`~book2skill.application.gate.Gate`. Format detection reloads
    on every call so newly installed format adapters are picked up.
    """

    def __init__(self, gate: Gate | None = None) -> None:
        self._gate = gate or Gate()

    def discover(
        self,
        inputs: list[str],
        *,
        glob: bool = True,
        recursive: bool = True,
    ) -> tuple[list[DiscoveredFile], list[GateError]]:
        """Discover and validate input files (see :meth:`Gate.discover`)."""
        return self._gate.discover(inputs, glob=glob, recursive=recursive)

    @staticmethod
    def sha256(path: str | Path) -> str:
        """Return the lowercase hex SHA-256 of a file's content."""
        digest = hashlib.sha256()
        with open(Path(path).resolve(), "rb") as fh:
            while chunk := fh.read(1 << 20):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def source_id_for(path: str | Path) -> str:
        """Return the stable content-derived ``source_id`` for a file."""
        digest = SourceService.sha256(path)
        return digest[:12]


class ExtractionService:
    """Invoke installed format adapters to produce normalized text blocks.

    Wraps :class:`~book2skill.extractors.registry.ExtractorRegistry`. The
    registry is resolved lazily so extensions can contribute new extractors
    before this service is first used.
    """

    def __init__(self) -> None:
        self._registry: ExtractorRegistry | None = None

    @property
    def registry(self) -> ExtractorRegistry:
        if self._registry is None:
            self._registry = default_registry()
        return self._registry

    def available_formats(self) -> list[SourceFormat]:
        """Return the formats for which an extractor is currently registered."""
        return self.registry.formats()

    def extract_text_blocks(self, path: str | Path) -> list[TextBlock]:
        """Extract sanitized text blocks from *path* via the probe-selected adapter."""
        resolve = Path(path).resolve()
        extractor = self.registry.probe(resolve)
        if extractor is None:
            raise LookupError(f"no extractor available for {resolve.name}")
        return extractor.extract_text_blocks(resolve)

    def extract(
        self,
        path: str | Path,
        *,
        source_id: str,
        original_name: str | None = None,
    ) -> tuple[SourceManifest, list[ExtractionMapEntry]]:
        """Extract a full manifest + extraction map using the probe-selected adapter."""
        resolve = Path(path).resolve()
        extractor = self.registry.probe(resolve)
        if extractor is None:
            raise LookupError(f"no extractor available for {resolve.name}")
        return extractor.extract(
            resolve,
            source_id=source_id,
            original_name=original_name,
        )


class StorageService:
    """Facade over the Raw / Schema / Wiki storage ports.

    Instances ``FileRawStorage``, ``FileSchemaStorage`` and ``FileWikiStorage``
    rooted at *data_home*. Extensions that need only a protocol may instead
    depend on the raw storage port types from :mod:`book2skill.sdk`.
    """

    def __init__(self, data_home: str | Path) -> None:
        root = Path(data_home).resolve()
        self.raw: RawStorage = FileRawStorage(root)
        self.schema: SchemaStorage = FileSchemaStorage(root)
        self.wiki: WikiStorage = FileWikiStorage(root)


class SkillCompilerService:
    """Compile :class:`SkillIR` from knowledge units and serialize it to a Skill dir.

    Wraps :class:`IRBuilder`, :class:`SkillWriter` and schema validation. The
    resulting Skill is host neutral; host overlays are applied by
    ``book2skill.extensions`` / host adapters.
    """

    def __init__(
        self,
        output_dir: str | Path | None = None,
        templates_dir: str | Path | None = None,
    ) -> None:
        self._output_dir = Path(output_dir) if output_dir else Path.cwd()
        self._templates_dir = (
            Path(templates_dir) if templates_dir else None
        )

    def build_ir(self, units: list[KnowledgeUnit], spec: SkillSpec) -> SkillIR:
        """Build a host-neutral :class:`SkillIR` from knowledge units."""
        return IRBuilder(units, spec).build()

    def validate_ir(self, ir: SkillIR) -> None:
        """Validate a :class:`SkillIR` against the published schema."""
        ir_builder.validate_skill_ir_against_schema(ir)

    def write(self, ir: SkillIR, output_dir: str | Path | None = None) -> Path:
        """Render *ir* as a directory and return its location."""
        target = Path(output_dir) if output_dir else self._output_dir
        writer = SkillWriter(target, templates_dir=self._templates_dir)
        return writer.write(ir)


class ValidatorRegistryService:
    """Run the standard validation suite against a compiled Skill directory."""

    def validate(self, skill_dir: str | Path) -> QualityReport:
        """Validate a compiled Skill and return its :class:`QualityReport`."""
        return Validator(Path(skill_dir).resolve()).validate()
