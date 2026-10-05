"""HTTP client for the Open-Meteo forecast API.

This module is the only place that knows about the API's URL, parameters and
response envelope. It performs transport-level handling (timeouts, retries,
HTTP errors) and structural validation of the response; field-level cleaning
belongs to the processing layer.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

from autobrief import __version__
from autobrief.config import Settings
from autobrief.exceptions import (
    APIHTTPError,
    APITimeoutError,
    InvalidResponseError,
    NetworkError,
)

logger = logging.getLogger(__name__)

DAILY_FIELDS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
)
CURRENT_FIELDS = ("temperature_2m", "relative_humidity_2m", "wind_speed_10m")
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class WeatherClient:
    """Fetches the current conditions and daily forecast for one location."""

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": f"autobrief/{__version__}"})
        self._sleep = sleep

    def build_params(self) -> dict[str, Any]:
        """Query parameters for the request (includes the API key if configured)."""
        s = self._settings
        params: dict[str, Any] = {
            "latitude": s.latitude,
            "longitude": s.longitude,
            "daily": ",".join(DAILY_FIELDS),
            "current": ",".join(CURRENT_FIELDS),
            "timezone": s.timezone,
            "forecast_days": s.forecast_days,
        }
        if s.api_key:
            params["apikey"] = s.api_key
        return params

    def fetch_forecast(self) -> dict[str, Any]:
        """Return the validated JSON payload.

        Raises:
            NetworkError: the server could not be reached.
            APITimeoutError: the request timed out (after retries).
            APIHTTPError: the API answered with an error status.
            InvalidResponseError: the body is not the expected JSON structure.
        """
        params = self.build_params()
        safe_params = {k: v for k, v in params.items() if k != "apikey"}
        attempts = self._settings.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            logger.info(
                "API request: GET %s params=%s (attempt %d/%d)",
                self._settings.api_base_url,
                safe_params,
                attempt,
                attempts,
            )
            try:
                payload = self._request_once(params)
            except (NetworkError, APITimeoutError, APIHTTPError) as exc:
                last_error = exc
                retryable = not isinstance(exc, APIHTTPError) or exc.status_code in RETRYABLE_STATUS
                if retryable and attempt < attempts:
                    delay = 2 ** (attempt - 1)
                    logger.warning("API call failed (%s); retrying in %ss", exc, delay)
                    self._sleep(delay)
                    continue
                logger.error("API call failed permanently: %s", exc)
                raise
            logger.info(
                "Data retrieved successfully: %d daily records",
                len(payload["daily"]["time"]),
            )
            return payload

        raise last_error  # pragma: no cover - loop always returns or raises

    def _request_once(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._session.get(
                self._settings.api_base_url, params=params, timeout=self._settings.request_timeout
            )
        except requests.exceptions.Timeout:
            raise APITimeoutError("API request timed out.") from None
        except requests.exceptions.RequestException as exc:
            # Message deliberately omits exc text: it can contain the full URL.
            logger.debug("Request exception type: %s", type(exc).__name__)
            raise NetworkError("Could not connect to the API server.") from None

        if response.status_code >= 400:
            raise APIHTTPError(
                f"API returned HTTP {response.status_code}{self._error_reason(response)}.",
                status_code=response.status_code,
            )
        return self._parse(response)

    @staticmethod
    def _error_reason(response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return ""
        reason = body.get("reason") if isinstance(body, dict) else None
        return f" ({reason})" if isinstance(reason, str) else ""

    @staticmethod
    def _parse(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            raise InvalidResponseError("API response was not valid JSON.") from None
        if not isinstance(payload, dict):
            raise InvalidResponseError("API response had an unexpected structure.")
        daily = payload.get("daily")
        if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
            raise InvalidResponseError("API response is missing the 'daily' forecast data.")
        return payload
