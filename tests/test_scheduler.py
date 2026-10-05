import threading
import time
from datetime import datetime, timedelta

import pytest
import responses

from autobrief.exceptions import APITimeoutError, SchedulerError
from autobrief.scheduler.scheduler import AutoBriefScheduler, parse_daily_time, parse_interval_minutes
from autobrief.utils.state import StateStore
from tests.conftest import API_URL, RAW_PAYLOAD


@pytest.mark.parametrize("value", ["09:00", "00:00", "23:59", "18:30"])
def test_valid_daily_times(value):
    assert parse_daily_time(value) == value


@pytest.mark.parametrize("value", ["9:00", "24:00", "12:60", "noon", "", "09:00:00", None])
def test_invalid_daily_times(value):
    with pytest.raises(SchedulerError) as info:
        parse_daily_time(value)
    assert "HH:MM" in info.value.hint


@pytest.mark.parametrize("value", [0, -5, 10081])
def test_invalid_intervals(value):
    with pytest.raises(SchedulerError):
        parse_interval_minutes(value)


def test_successful_job_reports_result(settings):
    results = []
    sched = AutoBriefScheduler(settings, job=lambda: None, on_result=lambda ok, msg: results.append(ok))
    sched.every_minutes(5)
    assert sched.run_now() is True and results == [True]
    assert sched.description == "every 5 minute(s)"


def test_failed_jobs_never_raise_and_scheduler_keeps_working(settings):
    outcomes = iter([APITimeoutError("API request timed out."), RuntimeError("bug"), "ok"])
    results = []

    def flaky():
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value

    sched = AutoBriefScheduler(settings, job=flaky, on_result=lambda ok, msg: results.append((ok, msg)))
    sched.every_minutes(1)

    assert [sched.run_now(), sched.run_now(), sched.run_now()] == [False, False, True]
    assert results[0] == (False, "API request timed out.")
    assert results[1][1].startswith("Unexpected RuntimeError")


def test_due_job_is_triggered_by_run_pending(settings):
    calls = []
    sched = AutoBriefScheduler(settings, job=lambda: calls.append(1))
    sched.every_day_at("09:00")
    sched.run_pending()
    assert calls == []  # not due yet

    sched.jobs[0].next_run = datetime.now() - timedelta(seconds=1)
    sched.run_pending()
    assert calls == [1]


def test_run_forever_requires_a_schedule(settings):
    with pytest.raises(SchedulerError, match="No schedule"):
        AutoBriefScheduler(settings).run_forever()


def test_run_forever_records_state_and_stops_cleanly(settings):
    sched = AutoBriefScheduler(settings, job=lambda: None)
    sched.every_day_at("09:00")
    stop = threading.Event()
    thread = threading.Thread(target=sched.run_forever, args=(stop, 0.01))
    store = StateStore(settings.state_file)

    thread.start()
    deadline = time.monotonic() + 3
    while not store.load()["scheduler"] and time.monotonic() < deadline:
        time.sleep(0.01)
    snapshot = store.load()["scheduler"]
    assert snapshot and snapshot["description"] == "daily at 09:00"
    assert StateStore.scheduler_is_active(snapshot)

    stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert store.load()["scheduler"] is None


def test_stale_heartbeat_is_not_active():
    assert not StateStore.scheduler_is_active({"heartbeat": "2020-01-01T00:00:00+00:00"})
    assert not StateStore.scheduler_is_active(None)


def test_real_workflow_failure_is_recorded_then_scheduler_recovers(settings, http):
    """Default job = full pipeline: first run fails (API down), next run succeeds."""
    from dataclasses import replace

    settings = replace(settings, max_retries=0)
    sched = AutoBriefScheduler(settings, fmt="html")
    sched.every_minutes(1)

    http.add(responses.GET, API_URL, status=503, json={"reason": "down"})
    assert sched.run_now() is False

    http.replace(responses.GET, API_URL, json=RAW_PAYLOAD)
    assert sched.run_now() is True

    state = StateStore(settings.state_file).load()
    assert state["runs"] == {"total": 2, "succeeded": 1, "failed": 1}
    assert state["last_run"]["trigger"] == "scheduled"
    assert len(list(settings.reports_dir.glob("*.html"))) == 1
