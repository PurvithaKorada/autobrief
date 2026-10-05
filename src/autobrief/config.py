"""Configuration loading and validation.

Settings come from environment variables (optionally populated from a ``.env``
file). Nothing is hardcoded except safe defaults, and secrets such as the
optional API key are only ever read from the environment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from autobrief.exceptions import ConfigError

DEFAULT_API_URL = "https://api.open-meteo.com/v1/forecast"
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass(frozen=True)
class Settings:
    """Immutable, validated application settings."""

    api_base_url: str
    location_name: str
    latitude: float
    longitude: float
    timezone: str
    forecast_days: int
    request_timeout: float
    max_retries: int
    api_key: str | None
    reports_dir: Path
    logs_dir: Path
    state_file: Path
    log_level: str

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "autobrief.log"

    def __repr__(self) -> str:  # never leak the API key
        key = "set" if self.api_key else "unset"
        return (
            f"Settings(api_base_url={self.api_base_url!r}, location={self.location_name!r}, "
            f"lat={self.latitude}, lon={self.longitude}, api_key={key})"
        )


def validate_latitude(value: float) -> float:
    if not -90.0 <= value <= 90.0:
        raise ValueError("latitude must be between -90 and 90")
    return value


def validate_longitude(value: float) -> float:
    if not -180.0 <= value <= 180.0:
        raise ValueError("longitude must be between -180 and 180")
    return value


def _number(env: Mapping[str, str], name: str, default: str, cast: type) -> float | int:
    raw = env.get(name, "").strip() or default
    try:
        return cast(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a valid {cast.__name__}, got {raw!r}.") from None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from the environment.

    When ``env`` is omitted, a ``.env`` file (searched from the current
    directory upwards) is loaded first and real environment variables win.
    """
    if env is None:
        load_dotenv(find_dotenv(usecwd=True), override=False)
        env = os.environ

    api_url = env.get("AUTOBRIEF_API_URL", "").strip() or DEFAULT_API_URL
    if not api_url.startswith(("http://", "https://")):
        raise ConfigError("AUTOBRIEF_API_URL must start with http:// or https://.")

    latitude = _number(env, "AUTOBRIEF_LATITUDE", "51.5072", float)
    longitude = _number(env, "AUTOBRIEF_LONGITUDE", "-0.1276", float)
    try:
        validate_latitude(latitude)
        validate_longitude(longitude)
    except ValueError as exc:
        raise ConfigError(f"Invalid coordinates: {exc}.") from None

    forecast_days = _number(env, "AUTOBRIEF_FORECAST_DAYS", "7", int)
    if not 1 <= forecast_days <= 16:
        raise ConfigError("AUTOBRIEF_FORECAST_DAYS must be between 1 and 16.")

    timeout = _number(env, "AUTOBRIEF_TIMEOUT", "10", float)
    if timeout <= 0:
        raise ConfigError("AUTOBRIEF_TIMEOUT must be greater than 0.")

    retries = _number(env, "AUTOBRIEF_MAX_RETRIES", "2", int)
    if not 0 <= retries <= 5:
        raise ConfigError("AUTOBRIEF_MAX_RETRIES must be between 0 and 5.")

    log_level = (env.get("AUTOBRIEF_LOG_LEVEL", "").strip() or "INFO").upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(f"AUTOBRIEF_LOG_LEVEL must be one of {', '.join(VALID_LOG_LEVELS)}.")

    logs_dir = Path(env.get("AUTOBRIEF_LOGS_DIR", "").strip() or "logs")
    state_raw = env.get("AUTOBRIEF_STATE_FILE", "").strip()

    return Settings(
        api_base_url=api_url,
        location_name=env.get("AUTOBRIEF_LOCATION", "").strip() or "London",
        latitude=latitude,
        longitude=longitude,
        timezone=env.get("AUTOBRIEF_TIMEZONE", "").strip() or "auto",
        forecast_days=forecast_days,
        request_timeout=timeout,
        max_retries=retries,
        api_key=env.get("AUTOBRIEF_API_KEY", "").strip() or None,
        reports_dir=Path(env.get("AUTOBRIEF_REPORTS_DIR", "").strip() or "reports"),
        logs_dir=logs_dir,
        state_file=Path(state_raw) if state_raw else logs_dir / "state.json",
        log_level=log_level,
    )
