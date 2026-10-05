"""Command-line interface for AutoBrief."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from autobrief import __version__
from autobrief.config import Settings, load_settings, validate_latitude, validate_longitude
from autobrief.exceptions import AutoBriefError
from autobrief.reports.generator import FORMATS
from autobrief.scheduler.scheduler import AutoBriefScheduler
from autobrief.utils.logger import setup_logging
from autobrief.utils.state import StateStore
from autobrief.workflow import run_workflow

logger = logging.getLogger(__name__)
RULE_WIDTH = 36


class Console:
    """Small helper for consistent, terminal-safe output."""

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdout
        encoding = getattr(self.stream, "encoding", None) or "ascii"
        try:
            "✓✗─".encode(encoding)
            self.ok_mark, self.fail_mark, self.rule_char = "✓", "✗", "─"
        except (UnicodeEncodeError, LookupError):
            self.ok_mark, self.fail_mark, self.rule_char = "OK", "x", "-"
        self.color = self.stream.isatty() and "NO_COLOR" not in os.environ

    def _paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def line(self, text: str = "") -> None:
        print(text, file=self.stream, flush=True)

    def header(self, title: str = "AutoBrief") -> None:
        self.line()
        self.line(self._paint(title, "1"))
        self.line(self.rule_char * RULE_WIDTH)

    def step(self, text: str) -> None:
        self.line(f"{self._paint(self.ok_mark, '32')} {text}")

    def error_block(self, title: str, reason: str, hint: str, show_log: Path | None = None) -> None:
        self.line(f"{self._paint(self.fail_mark, '31')} {title}")
        self.line()
        self.line(f"Reason: {reason}")
        self.line()
        self.line(hint)
        if show_log:
            self.line(f"See {show_log} for details.")


def _friendly_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(iso)

def _rel(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path.cwd()).as_posix()
    except ValueError:
        return Path(path).as_posix()


# ------------------------------------------------------------------ argparse
def _latitude(value: str) -> float:
    try:
        return validate_latitude(float(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc) if "between" in str(exc) else f"invalid number: {value!r}") from None


def _longitude(value: str) -> float:
    try:
        return validate_longitude(float(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc) if "between" in str(exc) else f"invalid number: {value!r}") from None


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid whole number: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or greater")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autobrief",
        description="AutoBrief - Automated Intelligence Report Generator.\n"
        "Fetches live weather forecast data, analyses it and writes HTML/PDF reports.",
        epilog="Run 'autobrief <command> --help' for command-specific options.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"autobrief {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=FORMATS, default="both", help="report format (default: both)")
    common.add_argument("--location", help="location name shown in the report (default: from config)")
    common.add_argument("--latitude", type=_latitude, help="latitude, -90 to 90 (default: from config)")
    common.add_argument("--longitude", type=_longitude, help="longitude, -180 to 180 (default: from config)")

    gen = sub.add_parser(
        "generate",
        parents=[common],
        help="fetch data and generate a report now",
        description="Fetch current data, analyse it and save a timestamped report in reports/.",
    )
    gen.set_defaults(func=cmd_generate)

    sch = sub.add_parser(
        "schedule",
        parents=[common],
        help="generate reports automatically on a schedule",
        description="Run in the foreground and generate reports on a schedule. Press Ctrl+C to stop.",
    )
    when = sch.add_mutually_exclusive_group(required=True)
    when.add_argument("--daily", metavar="HH:MM", help="run every day at this 24-hour time, e.g. 09:00")
    when.add_argument("--every", metavar="MINUTES", type=_positive_int, help="run every N minutes")
    sch.add_argument("--run-now", action="store_true", help="also generate one report immediately on start")
    sch.set_defaults(func=cmd_schedule)

    stat = sub.add_parser(
        "status",
        help="show configuration, last run and scheduler state",
        description="Show the last run, run counts and whether the scheduler is active.",
    )
    stat.set_defaults(func=cmd_status)

    logs = sub.add_parser("logs", help="show recent log entries", description="Print the end of logs/autobrief.log.")
    logs.add_argument("-n", "--lines", type=_positive_int, default=20, help="number of lines to show (default: 20)")
    logs.set_defaults(func=cmd_logs)
    return parser


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    changes = {}
    if getattr(args, "location", None):
        changes["location_name"] = args.location.strip()
    if getattr(args, "latitude", None) is not None:
        changes["latitude"] = args.latitude
    if getattr(args, "longitude", None) is not None:
        changes["longitude"] = args.longitude
    return replace(settings, **changes) if changes else settings


# ------------------------------------------------------------------ commands
def cmd_generate(args: argparse.Namespace, settings: Settings) -> int:
    ui = Console()
    ui.header()
    try:
        result = run_workflow(settings, args.format, trigger="manual", progress=ui.step)
    except AutoBriefError as exc:
        ui.line()
        ui.error_block("Report generation failed", str(exc), exc.hint, settings.log_file)
        return 1
    except Exception as exc:  # already logged with traceback by the workflow
        ui.line()
        ui.error_block(
            "Report generation failed",
            f"Unexpected error ({type(exc).__name__}).",
            "This looks like a bug.",
            settings.log_file,
        )
        return 1
    ui.line()
    ui.line("Report generated successfully!")
    ui.line()
    ui.line("Location:")
    for path in result.report_paths:
        ui.line(_rel(path))
    return 0


def cmd_schedule(args: argparse.Namespace, settings: Settings) -> int:
    ui = Console()

    def on_result(ok: bool, message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if ok:
            ui.line(f"[{stamp}] {ui.ok_mark} Report generated: {message}")
        else:
            ui.line(f"[{stamp}] {ui.fail_mark} Run failed: {message.rstrip('.')}. Will retry at the next trigger.")

    scheduler = AutoBriefScheduler(settings, args.format, on_result=on_result)
    try:
        if args.daily:
            scheduler.every_day_at(args.daily)
        else:
            scheduler.every_minutes(args.every)
        ui.header()
        ui.step(f"Scheduler started: {scheduler.description} (format: {args.format})")
        ui.line("Press Ctrl+C to stop.")
        ui.line()
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        if args.run_now:
            scheduler.run_now()
        scheduler.run_forever(stop)
    except AutoBriefError as exc:
        ui.error_block("Scheduler could not start", str(exc), exc.hint, settings.log_file)
        return 1
    ui.line()
    ui.line("Scheduler stopped.")
    return 0


def cmd_status(args: argparse.Namespace, settings: Settings) -> int:
    ui = Console()
    state = StateStore(settings.state_file).load()
    reports = sorted(settings.reports_dir.glob("autobrief_*.*")) if settings.reports_dir.is_dir() else []
    ui.header("AutoBrief status")
    rows = [
        ("Data source", f"Open-Meteo ({settings.location_name}, {settings.latitude}, {settings.longitude})"),
        ("Reports", f"{len(reports)} file(s) in {_rel(settings.reports_dir)}/"),
        ("Log file", _rel(settings.log_file)),
    ]
    sched = state.get("scheduler")
    active = StateStore.scheduler_is_active(sched)
    rows.append(("Scheduler", f"running - {sched['description']} (pid {sched['pid']})" if active else "not running"))
    runs = state["runs"]
    rows.append(("Total runs", f"{runs['total']} ({runs['succeeded']} succeeded, {runs['failed']} failed)"))
    last = state.get("last_run")
    if last:
        rows.append(
            (
                "Last run",
                f"{_friendly_time(last['timestamp'])} - {last['status']} "
                f"({last['trigger']}, {last['duration_seconds']}s)",
            )
        )
        if last["status"] == "failed":
            rows.append(("Last error", last.get("error") or "unknown"))
    else:
        rows.append(("Last run", "none yet - try 'autobrief generate'"))
    success = state.get("last_success")
    if success and success.get("reports"):
        rows.append(("Last report", success["reports"][0]))
    for label, value in rows:
        ui.line(f"{label:<12}: {value}")
    return 0


def cmd_logs(args: argparse.Namespace, settings: Settings) -> int:
    ui = Console()
    try:
        with settings.log_file.open(encoding="utf-8", errors="replace") as handle:
            tail = list(deque(handle, maxlen=args.lines))
    except FileNotFoundError:
        ui.line(f"No log file yet at {_rel(settings.log_file)}. Run 'autobrief generate' first.")
        return 0
    except OSError as exc:
        ui.error_block("Could not read log file", exc.strerror or type(exc).__name__, "Check file permissions.")
        return 1
    for entry in tail:
        ui.line(entry.rstrip("\n"))
    return 0


# ---------------------------------------------------------------------- main
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    ui = Console()
    try:
        settings = _apply_overrides(load_settings(), args)
    except AutoBriefError as exc:
        ui.error_block("Invalid configuration", str(exc), exc.hint)
        return 1

    setup_logging(settings)
    logger.info("AutoBrief started: version=%s command=%s", __version__, args.command)
    try:
        return args.func(args, settings)
    except KeyboardInterrupt:
        ui.line("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
