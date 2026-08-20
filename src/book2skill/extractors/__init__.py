"""Extractor package for Book2Skill."""

from book2skill.extractors.base import (
    ExtractionContractError,
    ExtractionResult,
    Extractor,
    ExtractorCapabilities,
    validate_extraction_result,
)
from book2skill.extractors.docx_extractor import DocxExtractor
from book2skill.extractors.epub_extractor import EpubExtractor
from book2skill.extractors.html_extractor import HtmlExtractor
from book2skill.extractors.mobi_extractor import MobiExtractor
from book2skill.extractors.pdf_extractor import PdfExtractor
from book2skill.extractors.registry import ExtractorRegistry, default_registry
from book2skill.extractors.rtf_extractor import RtfExtractor
from book2skill.extractors.text_extractor import TextExtractor

__all__ = [
    "Extractor",
    "ExtractorCapabilities",
    "ExtractionContractError",
    "ExtractionResult",
    "validate_extraction_result",
    "ExtractorRegistry",
    "default_registry",
    "DocxExtractor",
    "EpubExtractor",
    "HtmlExtractor",
    "MobiExtractor",
    "PdfExtractor",
    "RtfExtractor",
    "TextExtractor",
]
