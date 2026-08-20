"""Tests for opt-in local structured diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

from book2skill.diagnostics import configure, emit, start_run


def test_diagnostics_are_jsonl_correlated_and_redacted(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "events.jsonl"
    configure(log_path)
    try:
        run_id, transaction_id = start_run(
            run_id="run-test", transaction_id="tx-test"
        )
        emit(
            "fatal_error",
            code="LLM_FAILURE",
            stage="cli",
            message="sk-secret-value failed at /private/source.txt",
            details={
                "exception_type": "LLMRuntimeError",
                "path": "/private/source.txt",
                "prompt": "never persist",
            },
        )
    finally:
        configure(None)

    assert (run_id, transaction_id) == ("run-test", "tx-test")
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["event"] == "fatal_error"
    assert payload["code"] == "LLM_FAILURE"
    assert payload["run_id"] == "run-test"
    assert payload["transaction_id"] == "tx-test"
    assert payload["stage"] == "cli"
    assert "sk-secret-value" not in json.dumps(payload)
    assert "/private/source.txt" not in json.dumps(payload)
    assert "prompt" not in payload.get("details", {})
