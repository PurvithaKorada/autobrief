"""Exception hierarchy for AutoBrief.

Every error that is expected in normal operation derives from
:class:`AutoBriefError` and carries a user-facing ``hint`` so the CLI can show a
helpful message instead of a traceback.
"""

from __future__ import annotations


class AutoBriefError(Exception):
    """Base class for all expected AutoBrief errors."""

    default_hint = "See the log file for details."

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint or self.default_hint


class ConfigError(AutoBriefError):
    default_hint = "Check your .env file or AUTOBRIEF_* environment variables."


class APIError(AutoBriefError):
    """Base class for failures while talking to the data API."""


class NetworkError(APIError):
    default_hint = "Check your network connection and try again."


class APITimeoutError(APIError):
    default_hint = "Check your network connection, or increase AUTOBRIEF_TIMEOUT."


class APIHTTPError(APIError):
    default_hint = "The API rejected or failed the request. Try again later."

    def __init__(self, message: str, *, status_code: int | None = None, hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.status_code = status_code


class InvalidResponseError(APIError):
    default_hint = "The API returned unexpected data. Try again later."


class DataValidationError(AutoBriefError):
    default_hint = "The API data was incomplete. Try again later."


class EmptyDatasetError(DataValidationError):
    default_hint = "The API returned no usable records. Try again later."


class ReportError(AutoBriefError):
    default_hint = "Check that the reports directory exists and is writable."


class SchedulerError(AutoBriefError):
    default_hint = "Run 'autobrief schedule --help' for valid options."
