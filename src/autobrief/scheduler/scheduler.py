"""In-process scheduler built on the lightweight ``schedule`` library.

The scheduler is a foreground process: it triggers the report workflow at the
configured time/interval, logs every run, and keeps going after a failed run.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable

import schedule

from autobrief.config import Settings
from autobrief.exceptions import AutoBriefError, SchedulerError
from autobrief.utils.state import StateStore
from autobrief.workflow import run_workflow

logger = logging.getLogger(__name__)

HEARTBEAT_EVERY_SECONDS = 30
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

JobFn = Callable[[], object]
ResultCallback = Callable[[bool, str], None]


def parse_daily_time(value: str) -> str:
    """Validate a 24-hour ``HH:MM`` string and return it."""
    if not _TIME_RE.match(value or ""):
        raise SchedulerError(f"Invalid time {value!r}.", hint="Use 24-hour HH:MM format, e.g. --daily 09:00.")
    return value


def parse_interval_minutes(value: int) -> int:
    if not 1 <= value <= 10080:
        raise SchedulerError(f"Invalid interval {value!r} minutes.", hint="Use a whole number between 1 and 10080.")
    return value


class AutoBriefScheduler:
    """Runs ``job`` on a schedule and never lets one failure stop the loop."""

    def __init__(
        self,
        settings: Settings,
        fmt: str = "both",
        job: JobFn | None = None,
        on_result: ResultCallback | None = None,
    ) -> None:
        self._settings = settings
        self._fmt = fmt
        self._job = job or self._default_job
        self._on_result = on_result or (lambda ok, msg: None)
        self._scheduler = schedule.Scheduler()  # private instance: no global state
        self._store = StateStore(settings.state_file)
        self.description = "not configured"

    @property
    def jobs(self) -> list[schedule.Job]:
        return self._scheduler.get_jobs()

    def every_day_at(self, hhmm: str) -> None:
        self._scheduler.every().day.at(parse_daily_time(hhmm)).do(self.run_now)
        self.description = f"daily at {hhmm}"

    def every_minutes(self, minutes: int) -> None:
        self._scheduler.every(parse_interval_minutes(minutes)).minutes.do(self.run_now)
        self.description = f"every {minutes} minute(s)"

    def _default_job(self) -> object:
        return run_workflow(self._settings, self._fmt, trigger="scheduled")

    def run_now(self) -> bool:
        """Execute the job once. Returns True on success; never raises."""
        logger.info("Scheduler execution started (%s)", self.description)
        try:
            result = self._job()
        except AutoBriefError as exc:
            logger.error("Scheduled run failed: %s; will retry at the next trigger", exc)
            self._on_result(False, str(exc))
            return False
        except Exception as exc:  # a scheduler must survive any job failure
            logger.exception("Scheduled run crashed; will retry at the next trigger")
            self._on_result(False, f"Unexpected {type(exc).__name__} (see log)")
            return False
        paths = getattr(result, "report_paths", None) or []
        logger.info("Scheduler execution succeeded")
        self._on_result(True, ", ".join(str(p) for p in paths))
        return True

    def run_pending(self) -> None:
        self._scheduler.run_pending()

    def run_forever(self, stop: threading.Event | None = None, poll_seconds: float = 1.0) -> None:
        """Block, running due jobs, until ``stop`` is set."""
        if not self.jobs:
            raise SchedulerError("No schedule configured.")
        stop = stop or threading.Event()
        self._store.scheduler_started(self.description)
        logger.info("Scheduler started: %s", self.description)
        last_beat = time.monotonic()
        try:
            while not stop.is_set():
                self.run_pending()
                if time.monotonic() - last_beat >= HEARTBEAT_EVERY_SECONDS:
                    self._store.scheduler_heartbeat()
                    last_beat = time.monotonic()
                stop.wait(poll_seconds)
        finally:
            self._store.scheduler_stopped()
            logger.info("Scheduler stopped")
