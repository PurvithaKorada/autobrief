"""The end-to-end pipeline: fetch -> validate -> process -> report.

Both the ``generate`` command and the scheduler call :func:`run_workflow`, so
logging and run-history recording live in exactly one place.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from autobrief.api.client import WeatherClient
from autobrief.config import Settings
from autobrief.exceptions import AutoBriefError
from autobrief.processing.analyzer import AnalysisResult, analyze
from autobrief.reports.generator import ReportGenerator
from autobrief.utils.state import StateStore

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class WorkflowResult:
    report_paths: list[Path]
    analysis: AnalysisResult
    generated_at: datetime


def run_workflow(
    settings: Settings,
    fmt: str = "both",
    *,
    trigger: str = "manual",
    progress: ProgressCallback | None = None,
    client: WeatherClient | None = None,
) -> WorkflowResult:
    """Run the full pipeline once and record the outcome in the run history.

    ``progress`` is called with a short message after each completed stage.
    Any :class:`AutoBriefError` is logged, recorded and re-raised.
    """
    notify = progress or (lambda _msg: None)
    store = StateStore(settings.state_file)
    started = time.monotonic()
    logger.info("Report workflow started (trigger=%s, format=%s, location=%s)", trigger, fmt, settings.location_name)

    try:
        raw = (client or WeatherClient(settings)).fetch_forecast()
        notify("Data retrieved from Open-Meteo")

        analysis = analyze(raw, location=settings.location_name, source_url=settings.api_base_url)
        notify("Data validated and processed")
        notify("Insights generated")

        generated_at = datetime.now().astimezone()
        paths = ReportGenerator(settings.reports_dir).generate(analysis, fmt, generated_at)
        for path in paths:
            notify(f"{path.suffix[1:].upper()} report created")
    except Exception as exc:
        known = isinstance(exc, AutoBriefError)
        if known:
            logger.error("Report workflow failed: %s", exc)
        else:
            logger.exception("Report workflow crashed unexpectedly")
        store.record_run(
            success=False,
            trigger=trigger,
            duration=time.monotonic() - started,
            error=str(exc) if known else f"Unexpected {type(exc).__name__}",
        )
        raise

    store.record_run(success=True, trigger=trigger, reports=paths, duration=time.monotonic() - started)
    logger.info("Report workflow finished in %.1fs: %s", time.monotonic() - started, [str(p) for p in paths])
    return WorkflowResult(report_paths=paths, analysis=analysis, generated_at=generated_at)
