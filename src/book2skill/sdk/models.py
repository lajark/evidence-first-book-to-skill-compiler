"""Public data models for the Book2Skill SDK.

Downstream extensions must import only from :mod:`book2skill.sdk`. This module
re-exports the stable Core models onto a single public surface and defines the
public extension manifest contract (mirrors ``schemas/extension-manifest.schema.json``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Stable Core model re-exports -------------------------------------------
# These are declared in Core modules but exported here so extensions depend on
# one stable namespace rather than on Core internal module paths.
from book2skill.application.gate import DiscoveredFile
from book2skill.application.models import (
    AnalysisBundle,
    CandidateUnit,
    StructureEntry,
    SuggestedSkill,
)
from book2skill.compiler.ir_builder import SkillIR, SkillSpec, SkillUsage, WorkflowStep
from book2skill.domain import (
    Confidentiality,
    ConflictRecord,
    DomainError,
    ErrorCode,
    ExtractionMapEntry,
    KnowledgeCluster,
    KnowledgeRef,
    KnowledgeStatus,
    KnowledgeUnit,
    Locator,
    LocatorKind,
    ReviewItem,
    SourceFormat,
    SourceManifest,
    TextBlock,
    UnitKind,
)
from book2skill.extractors.base import Extractor, ExtractorCapabilities
from book2skill.storage.ports import RawStorage, SchemaStorage, WikiStorage

__all__ = [
    # domain
    "Confidentiality",
    "ConflictRecord",
    "DomainError",
    "ErrorCode",
    "ExtractionMapEntry",
    "KnowledgeCluster",
    "KnowledgeRef",
    "KnowledgeStatus",
    "KnowledgeUnit",
    "Locator",
    "LocatorKind",
    "ReviewItem",
    "SourceFormat",
    "SourceManifest",
    "TextBlock",
    "UnitKind",
    # application
    "AnalysisBundle",
    "CandidateUnit",
    "DiscoveredFile",
    "StructureEntry",
    "SuggestedSkill",
    # compiler
    "SkillIR",
    "SkillSpec",
    "SkillUsage",
    "WorkflowStep",
    # extractors / storage ports
    "Extractor",
    "ExtractorCapabilities",
    "RawStorage",
    "SchemaStorage",
    "WikiStorage",
    # extension contract
    "ExtensionDependency",
    "ExtensionManifest",
]


class ExtensionDependency(BaseModel):
    """A dependency an extension declares on another extension."""

    model_config = ConfigDict(extra="forbid")

    extension_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    version: str


class ExtensionManifest(BaseModel):
    """An extension's ``extension-manifest.json`` (public contract).

    Mirrors ``schemas/extension-manifest.schema.json``. Extensions ship this
    file at their package root; Core validates and installs them.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    extension_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    requires: dict[str, Any] = Field(
        default_factory=lambda: {"book2skill": "", "extensions": []}
    )
    entry_points: list[str] = Field(min_length=1)
    contributes: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    migrations: list[dict[str, Any]] = Field(default_factory=list)
    checksums_file: Literal["checksums.sha256"] = "checksums.sha256"

    @field_validator("requires")
    @classmethod
    def _validate_requires(cls, value: dict[str, Any]) -> dict[str, Any]:
        expected = {"book2skill", "extensions"}
        if set(value) != expected:
            raise ValueError("requires must contain only book2skill and extensions")
        if not isinstance(value["book2skill"], str):
            raise ValueError("requires.book2skill must be a string")
        extensions = value["extensions"]
        if not isinstance(extensions, list):
            raise ValueError("requires.extensions must be a list")
        for dependency in extensions:
            ExtensionDependency.model_validate(dependency)
        return value

    def book2skill_range(self) -> str:
        """Return the declared Core compatibility range (``requires.book2skill``)."""
        value = self.requires.get("book2skill", "")
        return str(value) if value is not None else ""

    def extension_dependencies(self) -> list[ExtensionDependency]:
        """Return declared dependencies on other extensions."""
        raw = self.requires.get("extensions", []) or []
        deps: list[ExtensionDependency] = []
        for item in raw:
            if isinstance(item, dict) and (
                "extension_id" in item and "version" in item
            ):
                deps.append(
                    ExtensionDependency(
                        extension_id=str(item["extension_id"]),
                        version=str(item["version"]),
                    )
                )
        return deps

    @classmethod
    def from_file(cls, path: str | Path) -> ExtensionManifest:
        """Load and validate a manifest from a JSON file on disk."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.model_validate(data)
