"""Shared test configuration.

The project root may contain a real ``.env`` with cloud-LLM credentials so
the installed CLI can be exercised against a live provider. Without an
explicit guard, any test that resolves the CLI adapter without an
``--llm`` flag would walk up from the project-root cwd, discover that
``.env``, and make live network calls — slow, costly and non-hermetic.

The autouse fixture below forces the offline Mock as the default provider
for the whole suite. It only sets the ``BOOK2SKILL_LLM`` environment
variable, which has higher priority than ``.env`` but lower than an
explicit CLI flag. Tests that assert ``.env``/shell resolution already
``delenv("BOOK2SKILL_LLM")`` (removing this default) and are unaffected.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _force_mock_llm_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to the offline Mock LLM so tests never make live calls.

    CLI flags (``--llm openai/compatible``) and tests that ``delenv`` this
    variable override the default; everyone else gets a hermetic Mock.
    """
    monkeypatch.setenv("BOOK2SKILL_LLM", "mock")
