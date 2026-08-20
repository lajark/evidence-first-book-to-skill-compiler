"""Opt-in, local structured diagnostics with conservative redaction.

Diagnostics never leave the machine and are disabled unless a caller supplies
``--diagnostic-log`` (or configures the module directly).  Records contain run
and transaction correlation IDs, stage, stable error code and a redacted
message; source bodies, prompts, credentials and full paths are excluded.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger("book2skill.diagnostics")
_LOGGER.propagate = False
_LOGGER.setLevel(logging.INFO)
_HANDLER: logging.Handler | None = None

_RUN_ID: ContextVar[str] = ContextVar("book2skill_run_id", default="-")
_TRANSACTION_ID: ContextVar[str] = ContextVar(
    "book2skill_transaction_id", default="-"
)
_STAGE: ContextVar[str] = ContextVar("book2skill_stage", default="cli")

_SECRET_RE = re.compile(
    r"(?i)(?:sk|key|token|secret)[-_][A-Za-z0-9._~-]{6,}"
)
_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\s\"']+")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "diagnostic_payload", {})
        event = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname.lower(),
            "run_id": _RUN_ID.get(),
            "transaction_id": _TRANSACTION_ID.get(),
            "stage": _STAGE.get(),
            **payload,
        }
        return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def configure(path: Path | None) -> None:
    """Enable or disable the local JSONL sink at *path*."""
    global _HANDLER
    if _HANDLER is not None:
        _LOGGER.removeHandler(_HANDLER)
        _HANDLER.close()
        _HANDLER = None
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(_JsonFormatter())
    _LOGGER.addHandler(handler)
    _HANDLER = handler


def start_run(
    *, run_id: str | None = None, transaction_id: str | None = None
) -> tuple[str, str]:
    """Start a correlation scope and return its redacted IDs."""
    resolved_run = run_id or f"run-{uuid.uuid4().hex[:16]}"
    resolved_transaction = transaction_id or f"tx-{uuid.uuid4().hex[:16]}"
    _RUN_ID.set(resolved_run)
    _TRANSACTION_ID.set(resolved_transaction)
    _STAGE.set("cli")
    return resolved_run, resolved_transaction


def set_stage(stage: str) -> None:
    """Set the current pipeline stage for subsequent diagnostic records."""
    _STAGE.set(stage or "cli")


def emit(
    event: str,
    *,
    code: str | None = None,
    stage: str | None = None,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Write one redacted event when diagnostics are configured."""
    if _HANDLER is None:
        return
    payload: dict[str, Any] = {"event": event}
    if code:
        payload["code"] = code
    if stage:
        payload["stage"] = stage
    if message:
        payload["message"] = _redact(message)
    if details:
        payload["details"] = {
            key: value
            for key, value in details.items()
            if key not in {"path", "full_path", "prompt", "body", "secret"}
            and isinstance(value, (str, int, float, bool, type(None)))
        }
    _LOGGER.info("diagnostic", extra={"diagnostic_payload": payload})


def _redact(value: str) -> str:
    """Remove obvious credentials and absolute paths from free-form text."""
    redacted = _SECRET_RE.sub("[REDACTED]", value)
    return _PATH_RE.sub("[PATH]", redacted)


__all__ = ["configure", "emit", "set_stage", "start_run"]
