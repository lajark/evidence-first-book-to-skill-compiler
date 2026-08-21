"""Single-job orchestration and progress fan-out for the local WebGUI."""

from __future__ import annotations

import queue
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from book2skill.application.progress import ProgressEvent


class JobBusyError(RuntimeError):
    """Raised when the desktop app already has one pipeline job running."""


class JobCancelledError(Exception):
    """Internal control-flow exception for a cooperative cancellation."""


# Short alias kept for the small public desktop control-flow contract.
JobCancelled = JobCancelledError


@dataclass
class _JobState:
    job_id: str
    kind: str
    cancel_event: threading.Event
    thread: threading.Thread | None = None


class EventBus:
    """Thread-safe fan-out bus used by SSE clients."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                # A disconnected or stalled browser must not block the
                # document pipeline.  The next event contains the latest
                # state and is therefore sufficient for the UI.
                continue


class JobContext:
    """Context passed to one service task."""

    def __init__(self, state: _JobState, events: EventBus) -> None:
        self.job_id = state.job_id
        self.kind = state.kind
        self._cancel_event = state.cancel_event
        self._events = events

    def check_cancelled(self) -> None:
        """Raise :class:`JobCancelled` at the next safe pipeline checkpoint."""

        if self._cancel_event.is_set():
            raise JobCancelled

    def progress(self, event: ProgressEvent) -> None:
        """Publish one serialisable, redacted progress event."""

        self.check_cancelled()
        payload = asdict(event)
        detail = str(payload.get("detail") or "")
        # Details may contain a source path.  Keep only the final component in
        # the UI stream so a local directory is never broadcast to clients.
        payload["detail"] = Path(detail).name if detail else ""
        self._events.publish({"type": "progress", "job_id": self.job_id, **payload})


class CancellationProgressReporter:
    """Adapter from the application's progress protocol to a JobContext."""

    def __init__(self, context: JobContext) -> None:
        self._context = context

    def __call__(self, stage: str, current: int, total: int, detail: str = "") -> None:
        self.on_progress_event(ProgressEvent(stage, current, total, detail))

    def on_progress_event(self, event: ProgressEvent) -> None:
        self._context.progress(event)


def redact_error(exc: BaseException) -> dict[str, str]:
    """Convert an exception to a safe UI payload without exposing paths."""

    code = getattr(getattr(exc, "code", None), "value", None) or getattr(
        exc, "code", None
    )
    message = getattr(exc, "message", None) or str(exc)
    recovery = getattr(exc, "recovery", None) or ""
    path_pattern = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\s,;]+")
    message = path_pattern.sub("<path>", message)
    recovery = path_pattern.sub("<path>", recovery)
    return {
        "code": str(code or "DESKTOP_JOB_FAILED"),
        "message": message[:1000],
        "recovery": recovery[:1000],
    }


class JobManager:
    """Run at most one desktop job and expose its status through EventBus."""

    def __init__(self) -> None:
        self.events = EventBus()
        self._lock = threading.Lock()
        self._active: _JobState | None = None

    def status(self) -> dict[str, object]:
        with self._lock:
            active = self._active
            return {
                "busy": active is not None,
                "job_id": active.job_id if active else None,
                "kind": active.kind if active else None,
            }

    def start(
        self,
        kind: str,
        runner: Callable[[JobContext], dict[str, object]],
    ) -> str:
        with self._lock:
            if self._active is not None:
                raise JobBusyError("A Book2Skill job is already running.")
            state = _JobState(
                job_id=uuid.uuid4().hex,
                kind=kind,
                cancel_event=threading.Event(),
            )
            self._active = state

        context = JobContext(state, self.events)
        thread = threading.Thread(
            target=self._run,
            args=(state, context, runner),
            name=f"book2skill-{kind}",
            daemon=True,
        )
        state.thread = thread
        self.events.publish({"type": "started", "job_id": state.job_id, "kind": kind})
        thread.start()
        return state.job_id

    def cancel(self) -> bool:
        with self._lock:
            if self._active is None:
                return False
            self._active.cancel_event.set()
            job_id = self._active.job_id
        self.events.publish({"type": "cancellation_requested", "job_id": job_id})
        return True

    def _run(
        self,
        state: _JobState,
        context: JobContext,
        runner: Callable[[JobContext], dict[str, object]],
    ) -> None:
        try:
            result = runner(context)
            context.check_cancelled()
        except JobCancelledError:
            self.events.publish({"type": "cancelled", "job_id": state.job_id})
        except Exception as exc:  # noqa: BLE001 - boundary converts to JSON
            self.events.publish(
                {"type": "failed", "job_id": state.job_id, "error": redact_error(exc)}
            )
        else:
            self.events.publish(
                {"type": "completed", "job_id": state.job_id, "result": result}
            )
        finally:
            with self._lock:
                if self._active is state:
                    self._active = None


__all__ = [
    "CancellationProgressReporter",
    "EventBus",
    "JobBusyError",
    "JobCancelled",
    "JobCancelledError",
    "JobContext",
    "JobManager",
    "redact_error",
]
