"""Regression tests for the pre-push sensitive-data scanner."""

from __future__ import annotations

from pathlib import Path

from scripts.pre_push_scan import _match_any, _scan_content


def test_env_example_is_an_allowed_template() -> None:
    assert _match_any(".env.example", [".env.*"]) is None
    assert _match_any("nested/.env.local", [".env.*"]) == ".env.*"


def test_placeholder_prefix_is_not_a_token(tmp_path: Path, monkeypatch) -> None:
    import scripts.pre_push_scan as scanner

    monkeypatch.setattr(scanner, "_REPO_ROOT", tmp_path)
    (tmp_path / "example.txt").write_text("LLM_API_KEY=sk-your-key\n", encoding="utf-8")

    assert _scan_content(["example.txt"]) == []


def test_long_token_is_detected_without_printing_value(
    tmp_path: Path, monkeypatch
) -> None:
    import scripts.pre_push_scan as scanner

    monkeypatch.setattr(scanner, "_REPO_ROOT", tmp_path)
    (tmp_path / "sample.txt").write_text(
        "LLM_API_KEY=" + "sk-" + "1234567890123456789012345\n", encoding="utf-8"
    )

    assert _scan_content(["sample.txt"]) == [("sample.txt", "content:openai-key")]
