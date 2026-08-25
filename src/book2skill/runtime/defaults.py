"""Deterministic defaults for production generated Skill products.

The defaults are intentionally task-centred: a product identity is derived
only from the validated Skill slug supplied by the caller.  No source title,
collection id or model output participates in the identity.
"""

from __future__ import annotations

import re

from book2skill.compiler import SkillSpec

from .emission import RuntimeClosureSpec
from .profiles import GeneratedSkillProduct, ProductProfile

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def default_runtime_contract(
    skill: SkillSpec | str,
) -> tuple[GeneratedSkillProduct, RuntimeClosureSpec]:
    """Return the stable standalone contract used by real Build/Publish.

    ``skill`` may be a :class:`SkillSpec` or its already validated slug.  A
    malformed slug is rejected instead of silently normalising a task into a
    different product identity.
    """

    slug = skill.name if isinstance(skill, SkillSpec) else skill
    slug = slug.strip()
    if not _SLUG.fullmatch(slug):
        raise ValueError("Skill name must be a lowercase hyphenated slug")
    product = GeneratedSkillProduct(
        product_id=f"skill.{slug}",
        task_id=slug,
        task_contract_id=f"task.{slug}",
        task_contract_version="1.0.0",
        profile=ProductProfile.STANDALONE,
    )
    closure = RuntimeClosureSpec(
        closure_version="1.0.0",
        skill_kernel_id=f"kernel.{slug}",
        skill_kernel_version="1.0.0",
        asset_pack_id=f"pack.{slug}",
        asset_pack_version="1.0.0",
        io_schema_id=f"io.{slug}",
        io_schema_version="1.0.0",
        security_profile_id="standalone-default",
        security_profile_version="1.0.0",
    )
    return product, closure


__all__ = ["default_runtime_contract"]
