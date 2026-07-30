"""Core domain models.

SourceManifest and ExtractionMapEntry are kept in the domain layer because they
are part of the ubiquitous language of Book2Skill and are independent of any
storage, CLI or adapter concern.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceFormat(StrEnum):
    """Supported source formats."""

    PDF = "pdf"
    EPUB = "epub"
    MOBI = "mobi"
    AZW = "azw"
    AZW3 = "azw3"
    TXT = "txt"
    MD = "md"
    DOCX = "docx"
    HTML = "html"
    RTF = "rtf"


class Confidentiality(StrEnum):
    """Confidentiality level for a source."""

    PERSONAL = "personal"
    INTERNAL = "internal"
    PUBLIC = "public"


class SourceManifest(BaseModel):
    """Immutable record of an ingested source."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    source_id: str = Field(..., min_length=8)
    version: int = Field(..., ge=1)
    original_name: str | None = None
    content_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")
    format: SourceFormat
    rights_confirmed: Literal[True] = True
    rights_note: str | None = None
    confidentiality: Confidentiality | None = None
    extractor: str | None = None
    extractor_version: str | None = None
    ingested_at: datetime
    supersedes: str | None = None

    @field_validator("ingested_at", mode="before")
    @classmethod
    def _ensure_tz(cls, value: datetime | str) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class LocatorKind(StrEnum):
    """Kind of source location."""

    PAGE = "page"
    CHAPTER = "chapter"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    SHEET = "sheet"
    UNKNOWN = "unknown"


class Locator(BaseModel):
    """Location of a text block within a source."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    kind: LocatorKind
    page: int | None = Field(None, ge=1)
    chapter: str | None = None
    paragraph: int | None = Field(None, ge=1)
    label: str | None = None


class ExtractionMapEntry(BaseModel):
    """Mapping between a normalized text block and its source location."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    block_id: str
    source_id: str
    text_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")
    locator: Locator
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class TextBlock(BaseModel):
    """A normalized text block with its source locator.

    Produced by extractors alongside :class:`ExtractionMapEntry` records so
    that downstream use cases (e.g. Analyze) can access the actual text
    content without re-reading the raw file.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    text: str = Field(..., min_length=1)
    locator: Locator
