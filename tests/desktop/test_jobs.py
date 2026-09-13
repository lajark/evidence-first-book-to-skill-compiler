from __future__ import annotations

import threading
import time

import pytest

from book2skill.desktop.jobs import JobBusyError, JobCancelled, JobManager


def _wait_for_event(manager: JobManager, event_type: str) -> dict[str, object]:
    subscriber = manager.events.subscribe()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            event = subscriber.get(timeout=0.1)
        except Exception:
            continue
        if event.get("type") == event_type:
            return event
    raise AssertionError(f"timed out waiting for {event_type}")


def test_job_manager_rejects_overlapping_jobs_and_publishes_completion() -> None:
    manager = JobManager()
    started = threading.Event()
    release = threading.Event()

    job_id = manager.start(
        "demo",
        lambda context: (started.set(), release.wait(1), {"ok": True})[-1],
    )
    assert job_id
    assert started.wait(1)
    with pytest.raises(JobBusyError):
        manager.start("other", lambda _context: {})

    release.set()
    event = _wait_for_event(manager, "completed")
    assert event["job_id"] == job_id
    assert event["result"] == {"ok": True}


def test_job_manager_cancel_is_cooperative() -> None:
    manager = JobManager()

    def run(context):
        while True:
            context.check_cancelled()
            time.sleep(0.005)

    job_id = manager.start("demo", run)
    assert manager.cancel() is True
    event = _wait_for_event(manager, "cancelled")
    assert event["job_id"] == job_id


def test_job_manager_replays_terminal_event_to_late_subscriber() -> None:
    manager = JobManager()
    job_id = manager.start("demo", lambda _context: {"ok": True})

    deadline = time.monotonic() + 2
    while manager.status()["busy"] and time.monotonic() < deadline:
        time.sleep(0.005)

    event = _wait_for_event(manager, "completed")
    assert event["job_id"] == job_id
    assert event["result"] == {"ok": True}


def test_job_cancelled_is_a_distinct_control_flow_exception() -> None:
    assert issubclass(JobCancelled, Exception)
