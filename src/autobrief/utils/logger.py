"""Application logging setup.

Logs go to a rotating file (``logs/autobrief.log``); the CLI prints its own
user-facing output, so nothing noisy reaches the terminal. A redacting
formatter masks anything that looks like a credential before it is written.
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler

from autobrief.config import Settings

LOGGER_NAME = "autobrief"
_SECRET_PATTERN = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)=([^&\s'\"]+)")
_HANDLER_TAG = "_autobrief_handler"


def redact(text: str) -> str:
    """Mask credential-like ``key=value`` pairs in ``text``."""
    return _SECRET_PATTERN.sub(r"\1=***", text)


class RedactingFormatter(logging.Formatter):
    """Formatter that masks secrets in messages and tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(settings: Settings) -> logging.Logger:
    """Configure the ``autobrief`` logger (idempotent) and return it."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, settings.log_level))
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            logger.removeHandler(handler)
            handler.close()

    try:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            settings.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError as exc:
        print(f"Warning: cannot write log file ({exc.strerror}); continuing without file logging.", file=sys.stderr)
        handler = logging.NullHandler()

    handler.setFormatter(RedactingFormatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))
    setattr(handler, _HANDLER_TAG, True)
    logger.addHandler(handler)
    return logger
