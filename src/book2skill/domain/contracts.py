"""Shared version locks for portable Agent Skill contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

AGENT_SKILLS_SPEC_REVISION = "217be548739f21d6008915c29aefe320ea1a90af"
AGENT_SKILLS_SPEC_VERIFIED_ON = "2026-08-06"
SKILLS_REF_VERSION = "0.1.0"
SKILL_VALIDATOR_VERSION = "1.5.6"


class TargetSpec(BaseModel):
    """Exact specification identity used for compilation and validation."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["agent-skills"] = "agent-skills"
    revision: str = AGENT_SKILLS_SPEC_REVISION
    verified_on: str = AGENT_SKILLS_SPEC_VERIFIED_ON
    reference_validator: str = f"skills-ref=={SKILLS_REF_VERSION}"


DEFAULT_TARGET_SPEC = TargetSpec()

__all__ = [
    "AGENT_SKILLS_SPEC_REVISION",
    "AGENT_SKILLS_SPEC_VERIFIED_ON",
    "DEFAULT_TARGET_SPEC",
    "SKILLS_REF_VERSION",
    "SKILL_VALIDATOR_VERSION",
    "TargetSpec",
]
