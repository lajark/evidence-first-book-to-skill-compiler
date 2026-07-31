"""Tests for the bootstrap self-test script (P2 元 Skill 自举)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from bootstrap_self import (  # type: ignore[import-not-found]  # noqa: E402
    _SOURCE_DOCS,
    run_bootstrap,
)


def test_bootstrap_completes_and_produces_skill(tmp_path: Path) -> None:
    """The pipeline can compile Book2Skill's own docs into a Skill.

    Skipped when source docs are not present (e.g., clean install without
    development process docs).
    """
    project_root = Path(__file__).resolve().parent.parent

    # Check if all source docs exist before running.
    missing = [doc for doc in _SOURCE_DOCS if not (project_root / doc).exists()]
    if missing:
        pytest.skip(
            f"Bootstrap source docs not present (clean install): {missing}"
        )

    result = run_bootstrap(project_root, output_dir=tmp_path)

    assert result["success"] is True
    assert result["stage"] == "validate"
    assert "skill_dir" in result
    skill_dir = Path(result["skill_dir"])
    assert skill_dir.exists()
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "provenance.yml").exists()
    # Wiki layer produced (chapters at minimum, from framework/principle units).
    assert (skill_dir / "wiki").is_dir()
    assert result["source_count"] == 5  # PRD + ARCH + DATA_MODEL + SKILL_STD + AGENTS
    assert result["collection_id"].startswith("col-")


def test_bootstrap_missing_docs_fails_gracefully(tmp_path: Path) -> None:
    """When source docs are missing, the bootstrap reports failure."""
    result = run_bootstrap(tmp_path, output_dir=tmp_path)
    assert result["success"] is False
    assert result["stage"] == "discover"
    assert "Missing source docs" in result["error"]
