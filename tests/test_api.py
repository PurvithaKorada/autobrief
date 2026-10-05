import logging

import pytest
import requests
import responses

from autobrief.api.client import WeatherClient
from autobrief.config import load_settings
from autobrief.exceptions import APIHTTPError, APITimeoutError, InvalidResponseError, NetworkError
from tests.conftest import API_URL, RAW_PAYLOAD


def make_client(settings):
    sleeps: list[float] = []
    return WeatherClient(settings, sleep=sleeps.append), sleeps


def test_fetch_returns_payload_and_sends_expected_query(settings, http):
    http.add(responses.GET, API_URL, json=RAW_PAYLOAD)
    client, _ = make_client(settings)

    payload = client.fetch_forecast()

    assert payload["daily"]["time"][0] == "2026-10-04"
    request = http.calls[0].request
    assert "latitude=51.5072" in request.url and "forecast_days=7" in request.url
    assert "precipitation_sum" in request.url
    assert request.headers["User-Agent"].startswith("autobrief/")


def test_timeout_is_retried_then_raises(settings, http):
    http.add(responses.GET, API_URL, body=requests.exceptions.Timeout())
    client, sleeps = make_client(settings)  # default max_retries=2 -> 3 attempts

    with pytest.raises(APITimeoutError):
        client.fetch_forecast()

    assert len(http.calls) == 3
    assert sleeps == [1, 2]  # exponential backoff between attempts


def test_server_error_then_success_recovers(settings, http):
    http.add(responses.GET, API_URL, status=503, json={"error": True, "reason": "busy"})
    http.add(responses.GET, API_URL, json=RAW_PAYLOAD)
    client, sleeps = make_client(settings)

    assert client.fetch_forecast()["timezone"] == "Europe/London"
    assert len(http.calls) == 2 and sleeps == [1]


def test_client_error_is_not_retried_and_reports_reason(settings, http):
    http.add(responses.GET, API_URL, status=400, json={"error": True, "reason": "Latitude must be in range"})
    client, sleeps = make_client(settings)

    with pytest.raises(APIHTTPError) as info:
        client.fetch_forecast()

    assert info.value.status_code == 400
    assert "Latitude must be in range" in str(info.value)
    assert len(http.calls) == 1 and sleeps == []


def test_connection_failure_raises_network_error(settings, http):
    http.add(responses.GET, API_URL, body=requests.exceptions.ConnectionError("dns failure"))
    client, _ = make_client(settings)

    with pytest.raises(NetworkError):
        client.fetch_forecast()


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("<html>oops</html>", id="not-json"),
        pytest.param('["a", "list"]', id="json-list"),
        pytest.param('{"hello": "world"}', id="no-daily"),
        pytest.param('{"daily": {"no_time": []}}', id="daily-without-time"),
    ],
)
def test_invalid_response_bodies_are_rejected(settings, http, body):
    http.add(responses.GET, API_URL, body=body, status=200)
    client, _ = make_client(settings)

    with pytest.raises(InvalidResponseError):
        client.fetch_forecast()


def test_api_key_is_sent_but_never_logged(tmp_path, http, caplog):
    secret = "SUPERSECRETKEY123"
    settings = load_settings(
        env={
            "AUTOBRIEF_API_URL": API_URL,
            "AUTOBRIEF_API_KEY": secret,
            "AUTOBRIEF_REPORTS_DIR": str(tmp_path),
            "AUTOBRIEF_LOGS_DIR": str(tmp_path),
        }
    )
    http.add(responses.GET, API_URL, json=RAW_PAYLOAD)
    client, _ = make_client(settings)

    with caplog.at_level(logging.DEBUG, logger="autobrief"):
        client.fetch_forecast()

    assert f"apikey={secret}" in http.calls[0].request.url
    assert secret not in caplog.text
    assert secret not in repr(settings)
