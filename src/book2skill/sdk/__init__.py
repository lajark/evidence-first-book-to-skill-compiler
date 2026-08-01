"""Book2Skill Extension SDK.

The SDK is the only public, stable surface downstream extensions may import.
Everything under :mod:`book2skill.sdk` is versioned and backwards compatible
within a Core major version; everything else in :mod:`book2skill` is private
implementation.

Extensions import the SDK, never Core internals::

    from book2skill.sdk import SourceService, ExtensionManifest
"""

from __future__ import annotations

from book2skill.sdk.context import ExtensionContext
from book2skill.sdk.models import (
    AnalysisBundle,
    CandidateUnit,
    Confidentiality,
    ConflictRecord,
    DiscoveredFile,
    DomainError,
    ErrorCode,
    ExtensionDependency,
    ExtensionManifest,
    ExtractionMapEntry,
    Extractor,
    ExtractorCapabilities,
    KnowledgeCluster,
    KnowledgeRef,
    KnowledgeUnit,
    Locator,
    LocatorKind,
    RawStorage,
    ReviewItem,
    SchemaStorage,
    SkillIR,
    SkillSpec,
    SkillUsage,
    SourceFormat,
    SourceManifest,
    StructureEntry,
    SuggestedSkill,
    TextBlock,
    UnitKind,
    WikiStorage,
    WorkflowStep,
)
from book2skill.sdk.services import (
    ExtractionService,
    SkillCompilerService,
    SourceService,
    StorageService,
    ValidatorRegistryService,
)

__all__ = [
    # services
    "SourceService",
    "ExtractionService",
    "StorageService",
    "SkillCompilerService",
    "ValidatorRegistryService",
    # context
    "ExtensionContext",
    # models & contracts
    "ExtensionManifest",
    "ExtensionDependency",
    "AnalysisBundle",
    "CandidateUnit",
    "Confidentiality",
    "ConflictRecord",
    "DiscoveredFile",
    "DomainError",
    "ErrorCode",
    "ExtractionMapEntry",
    "Extractor",
    "ExtractorCapabilities",
    "KnowledgeCluster",
    "KnowledgeRef",
    "KnowledgeUnit",
    "Locator",
    "LocatorKind",
    "RawStorage",
    "ReviewItem",
    "SchemaStorage",
    "SkillIR",
    "SkillSpec",
    "SkillUsage",
    "SourceFormat",
    "SourceManifest",
    "StructureEntry",
    "SuggestedSkill",
    "TextBlock",
    "UnitKind",
    "WikiStorage",
    "WorkflowStep",
]
