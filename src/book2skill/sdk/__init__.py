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
    KnowledgeStatus,
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
from book2skill.sdk.registrar import (
    ContributionRegistrationError,
    ExtensionContributionRegistry,
    ExtensionRegistrar,
)
from book2skill.sdk.services import (
    ExtractionService,
    SkillCompilerService,
    SourceService,
    StorageService,
    ValidatorRegistryService,
)

# Independently versioned public surface. Additive changes retain the same
# major version; incompatible changes require a Core major-version migration.
SDK_VERSION = "0.1"

__all__ = [
    "SDK_VERSION",
    # services
    "SourceService",
    "ExtractionService",
    "StorageService",
    "SkillCompilerService",
    "ValidatorRegistryService",
    # context
    "ExtensionContext",
    "ExtensionContributionRegistry",
    "ExtensionRegistrar",
    "ContributionRegistrationError",
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
    "KnowledgeStatus",
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
