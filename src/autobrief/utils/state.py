"""Tiny JSON-backed run history used by ``autobrief status``.

Writes are atomic (temp file + rename). A missing or corrupt file never breaks
the application; it just resets to an empty state.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HEARTBEAT_STALE_AFTER = timedelta(seconds=90)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _empty() -> dict[str, Any]:
    return {
        "runs": {"total": 0, "succeeded": 0, "failed": 0},
        "last_run": None,
        "last_success": None,
        "scheduler": None,
    }


class StateStore:
    """Persists run counts, the last run outcome and scheduler heartbeat."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _empty()
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("State file unreadable (%s); starting with empty state", type(exc).__name__)
            return _empty()
        if not isinstance(data, dict):
            return _empty()
        return {**_empty(), **data}

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("Could not save state file %s: %s", self.path, exc)

    def record_run(
        self,
        *,
        success: bool,
        trigger: str,
        reports: Sequence[Path] = (),
        error: str | None = None,
        duration: float = 0.0,
    ) -> None:
        data = self.load()
        data["runs"]["total"] += 1
        data["runs"]["succeeded" if success else "failed"] += 1
        entry = {
            "timestamp": _now(),
            "status": "success" if success else "failed",
            "trigger": trigger,
            "reports": [str(p) for p in reports],
            "error": error,
            "duration_seconds": round(duration, 2),
        }
        data["last_run"] = entry
        if success:
            data["last_success"] = entry
        self._save(data)

    def scheduler_started(self, description: str) -> None:
        data = self.load()
        data["scheduler"] = {"description": description, "pid": os.getpid(), "started_at": _now(), "heartbeat": _now()}
        self._save(data)

    def scheduler_heartbeat(self) -> None:
        data = self.load()
        if data.get("scheduler"):
            data["scheduler"]["heartbeat"] = _now()
            self._save(data)

    def scheduler_stopped(self) -> None:
        data = self.load()
        data["scheduler"] = None
        self._save(data)

    @staticmethod
    def scheduler_is_active(scheduler: dict[str, Any] | None) -> bool:
        """True if the recorded scheduler heartbeat is recent."""
        if not scheduler:
            return False
        try:
            beat = datetime.fromisoformat(scheduler["heartbeat"])
        except (KeyError, ValueError):
            return False
        return datetime.now().astimezone() - beat < HEARTBEAT_STALE_AFTER
