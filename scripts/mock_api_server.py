"""Offline mock of the Open-Meteo forecast endpoint (DEVELOPMENT ONLY).

Serves SYNTHETIC data in the same JSON shape as the real API so the full
AutoBrief workflow can be exercised without internet access. It is not part of
the installed package.

    python scripts/mock_api_server.py            # listens on 127.0.0.1:8765
    AUTOBRIEF_API_URL=http://127.0.0.1:8765/v1/forecast autobrief generate

Failure modes for testing error handling (change the URL path):
    /v1/forecast   valid data          /http500   HTTP 500
    /slow          responds after 5 s  /garbage   200 with invalid JSON
    /bad-shape     200 without 'daily' /missing   200 with a field missing
"""

from __future__ import annotations

import json
import math
import sys
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


def build_payload(days: int = 7) -> dict:
    start = date.today()
    times = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    highs = [round(17 + 4 * math.sin(i / 1.8) + i * 0.3, 1) for i in range(days)]
    lows = [round(h - 7.5 - (i % 3) * 0.6, 1) for i, h in enumerate(highs)]
    rain = [0.0, 0.4, 3.2, 7.8, 1.1, 0.0, 0.2, 2.5, 0.0, 0.0, 0.0, 4.1, 0.0, 0.0, 1.0, 0.0][:days]
    wind = [14.2, 18.5, 27.9, 41.3, 22.0, 16.4, 12.8, 19.9, 15.0, 11.2, 13.3, 24.7, 17.1, 12.0, 20.5, 14.8][:days]
    return {
        "latitude": 51.5,
        "longitude": -0.12,
        "timezone": "Europe/London",
        "elevation": 23.0,
        "current_units": {"temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h"},
        "current": {
            "time": times[0] + "T09:00",
            "temperature_2m": 13.8,
            "relative_humidity_2m": 76,
            "wind_speed_10m": 11.5,
        },
        "daily_units": {
            "time": "iso8601",
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "precipitation_sum": "mm",
            "wind_speed_10m_max": "km/h",
        },
        "daily": {
            "time": times,
            "temperature_2m_max": highs,
            "temperature_2m_min": lows,
            "precipitation_sum": rain,
            "wind_speed_10m_max": wind,
        },
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        parsed = urlparse(self.path)
        days = int(parse_qs(parsed.query).get("forecast_days", ["7"])[0])
        if parsed.path == "/v1/forecast":
            self._send(200, json.dumps(build_payload(days)).encode())
        elif parsed.path == "/http500":
            self._send(500, json.dumps({"error": True, "reason": "mock server failure"}).encode())
        elif parsed.path == "/slow":
            time.sleep(5)
            self._send(200, json.dumps(build_payload(days)).encode())
        elif parsed.path == "/garbage":
            self._send(200, b"<html>not json</html>", "text/html")
        elif parsed.path == "/bad-shape":
            self._send(200, json.dumps({"hello": "world"}).encode())
        elif parsed.path == "/missing":
            payload = build_payload(days)
            del payload["daily"]["precipitation_sum"]
            self._send(200, json.dumps(payload).encode())
        else:
            self._send(404, json.dumps({"error": True, "reason": "not found"}).encode())

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[mock-api] " + fmt % args + "\n")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Mock API listening on http://127.0.0.1:{port}/v1/forecast (synthetic data)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
