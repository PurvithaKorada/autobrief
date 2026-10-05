"""Shared fixtures. No test touches the live internet."""

from __future__ import annotations

import copy
import logging

import pytest
import responses

from autobrief.config import Settings, load_settings
from autobrief.processing.analyzer import analyze
from autobrief.utils.logger import LOGGER_NAME

API_URL = "https://api.example.test/v1/forecast"

# 7 days, Sun 04 Oct 2026 .. Sat 10 Oct 2026. Values chosen so statistics are easy to verify by hand:
#   daily means  = 12, 13.5, 15, 17, 15, 11, 10  -> average 13.357 (13.4)
#   peak high    = 22.0 on Wed 07 Oct          total rain = 16.9 mm over 4 wet days (>= 1 mm)
#   wettest day  = Thu 08 Oct (8.1 mm)         max wind   = 45.0 km/h on Tue 06 Oct
RAW_PAYLOAD = {
    "latitude": 51.5,
    "longitude": -0.12,
    "timezone": "Europe/London",
    "daily_units": {"temperature_2m_max": "°C", "precipitation_sum": "mm", "wind_speed_10m_max": "km/h"},
    "current": {"time": "2026-10-04T09:00", "temperature_2m": 14.2, "relative_humidity_2m": 71, "wind_speed_10m": 12.0},
    "daily": {
        "time": [f"2026-10-{d:02d}" for d in range(4, 11)],
        "temperature_2m_max": [16, 18, 20, 22, 19, 15, 14],
        "temperature_2m_min": [8, 9, 10, 12, 11, 7, 6],
        "precipitation_sum": [0, 2.5, 0.2, 5, 8.1, 1.1, 0],
        "wind_speed_10m_max": [15, 20, 45, 10, 22, 30, 18],
    },
}


@pytest.fixture
def raw_payload() -> dict:
    return copy.deepcopy(RAW_PAYLOAD)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return load_settings(
        env={
            "AUTOBRIEF_API_URL": API_URL,
            "AUTOBRIEF_REPORTS_DIR": str(tmp_path / "reports"),
            "AUTOBRIEF_LOGS_DIR": str(tmp_path / "logs"),
            "AUTOBRIEF_LOCATION": "Testville",
        }
    )


@pytest.fixture
def analysis(raw_payload):
    return analyze(raw_payload, location="Testville", source_url=API_URL)


@pytest.fixture
def http():
    """Intercepts every `requests` call; unmatched URLs raise instead of hitting the network."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Run the CLI inside an isolated temp directory with a clean AUTOBRIEF_* environment."""
    import os

    for name in [n for n in os.environ if n.startswith("AUTOBRIEF_")]:
        monkeypatch.delenv(name)
    monkeypatch.setattr("autobrief.config.load_dotenv", lambda *a, **k: False)  # ignore any real .env
    monkeypatch.setenv("AUTOBRIEF_API_URL", API_URL)
    monkeypatch.setenv("AUTOBRIEF_MAX_RETRIES", "0")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _release_log_handlers():
    yield
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
