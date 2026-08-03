"""Tests for installed-package resource resolution."""

from __future__ import annotations

from book2skill.resources import schema_file, template_file


def test_resource_layer_exposes_compiler_schema() -> None:
    schema = schema_file("skill-ir.schema.json")

    assert schema.name == "skill-ir.schema.json"
    assert '"$schema"' in schema.read_text(encoding="utf-8")


def test_resource_layer_exposes_skill_template() -> None:
    template = template_file("SKILL.md")

    assert template.name == "SKILL.md"
    assert "<skill-name>" in template.read_text(encoding="utf-8")
