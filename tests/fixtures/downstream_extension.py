"""Minimal downstream extension type-compatibility fixture.

This module deliberately imports only the published SDK namespace. It is
checked with mypy by the P1-05 verification command.
"""

from __future__ import annotations

from pathlib import Path

from book2skill.sdk import (
    KnowledgeStatus,
    KnowledgeUnit,
    SkillCompilerService,
    SkillSpec,
)


def compile_extension_draft(
    compiler: SkillCompilerService,
    units: list[KnowledgeUnit],
    spec: SkillSpec,
) -> Path:
    """Compile an extension-owned draft without importing Core internals."""
    return compiler.compile(units, spec)


def is_approved(unit: KnowledgeUnit) -> bool:
    """Use the public review-status enum without importing Core internals."""
    return unit.review_status is KnowledgeStatus.APPROVED
