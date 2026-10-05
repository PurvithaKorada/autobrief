import logging
from pathlib import Path

import pytest

from autobrief.config import DEFAULT_API_URL, load_settings
from autobrief.exceptions import ConfigError
from autobrief.utils.logger import redact, setup_logging


def test_defaults_when_nothing_is_set():
    s = load_settings(env={})
    assert s.api_base_url == DEFAULT_API_URL
    assert (s.forecast_days, s.max_retries, s.request_timeout) == (7, 2, 10.0)
    assert s.reports_dir == Path("reports") and s.log_file == Path("logs/autobrief.log")
    assert s.state_file == Path("logs/state.json") and s.api_key is None


def test_environment_overrides_and_blank_values_fall_back():
    s = load_settings(
        env={
            "AUTOBRIEF_LOCATION": "Tokyo",
            "AUTOBRIEF_LATITUDE": "35.68",
            "AUTOBRIEF_LONGITUDE": "139.69",
            "AUTOBRIEF_FORECAST_DAYS": "5",
            "AUTOBRIEF_LOGS_DIR": "/var/log/ab",
            "AUTOBRIEF_LOG_LEVEL": "debug",
            "AUTOBRIEF_TIMEOUT": "  ",
        }
    )
    assert (s.location_name, s.latitude, s.longitude, s.forecast_days) == ("Tokyo", 35.68, 139.69, 5)
    assert s.log_file == Path("/var/log/ab/autobrief.log") and s.log_level == "DEBUG"
    assert s.request_timeout == 10.0


@pytest.mark.parametrize(
    "name,value,fragment",
    [
        ("AUTOBRIEF_LATITUDE", "91", "latitude"),
        ("AUTOBRIEF_LONGITUDE", "-181", "longitude"),
        ("AUTOBRIEF_LATITUDE", "north", "AUTOBRIEF_LATITUDE"),
        ("AUTOBRIEF_FORECAST_DAYS", "0", "FORECAST_DAYS"),
        ("AUTOBRIEF_FORECAST_DAYS", "17", "FORECAST_DAYS"),
        ("AUTOBRIEF_TIMEOUT", "0", "TIMEOUT"),
        ("AUTOBRIEF_MAX_RETRIES", "9", "MAX_RETRIES"),
        ("AUTOBRIEF_API_URL", "ftp://x", "http"),
        ("AUTOBRIEF_LOG_LEVEL", "LOUD", "LOG_LEVEL"),
    ],
)
def test_invalid_values_raise_config_error(name, value, fragment):
    with pytest.raises(ConfigError, match=fragment):
        load_settings(env={name: value})


def test_api_key_is_hidden_in_repr():
    s = load_settings(env={"AUTOBRIEF_API_KEY": "topsecret"})
    assert s.api_key == "topsecret" and "topsecret" not in repr(s) and "api_key=set" in repr(s)


def test_redact_masks_credentials():
    assert redact("GET /x?apikey=abc123&days=7") == "GET /x?apikey=***&days=7"
    assert redact("token=xyz secret=1 password=hunter2") == "token=*** secret=*** password=***"
    assert redact("nothing sensitive") == "nothing sensitive"


def test_log_file_is_written_redacted_and_not_duplicated(settings):
    setup_logging(settings)
    logger = setup_logging(settings)  # second call must not add a second handler
    logging.getLogger("autobrief.test").info("calling https://x?apikey=SECRET1")

    for handler in logger.handlers:
        handler.flush()
    lines = settings.log_file.read_text().splitlines()
    assert len(lines) == 1 and "SECRET1" not in lines[0] and "apikey=***" in lines[0]
    assert "| INFO     | autobrief.test |" in lines[0]
