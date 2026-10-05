"""Cleaning, validation and analysis of the raw forecast payload.

Turns the API's column-oriented JSON into a tidy :class:`pandas.DataFrame`,
computes statistics and trends, and writes plain-language insights.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from autobrief.exceptions import DataValidationError, EmptyDatasetError

logger = logging.getLogger(__name__)

SOURCE_NAME = "Open-Meteo Forecast API"
WET_DAY_MM = 1.0  # a day counts as "wet" from this much precipitation
STRONG_WIND_KMH = 40.0  # flag wind at or above this speed
TREND_THRESHOLD = 0.3  # degrees per day that counts as warming / cooling

COLUMN_MAP = {
    "time": "date",
    "temperature_2m_max": "temp_max",
    "temperature_2m_min": "temp_min",
    "precipitation_sum": "precipitation",
    "wind_speed_10m_max": "wind_max",
}
NUMERIC_COLUMNS = ["temp_max", "temp_min", "precipitation", "wind_max"]


@dataclass(frozen=True)
class Metric:
    """One headline number shown on the report."""

    label: str
    value: str
    detail: str


@dataclass
class AnalysisResult:
    """Everything the report generator needs."""

    location: str
    latitude: float
    longitude: float
    timezone: str
    source_name: str
    source_url: str
    units: dict[str, str]
    frame: pd.DataFrame
    metrics: list[Metric]
    insights: list[str]
    summary: str
    trend_per_day: float
    current: dict[str, float] = field(default_factory=dict)
    dropped_rows: int = 0


def build_dataframe(raw: dict[str, Any]) -> tuple[pd.DataFrame, int]:
    """Validate and clean the ``daily`` block.

    Returns the cleaned frame and the number of rows that had to be dropped.

    Raises:
        DataValidationError: required fields are missing or inconsistent.
        EmptyDatasetError: no usable rows remain.
    """
    daily = raw.get("daily")
    if not isinstance(daily, dict):
        raise DataValidationError("Response has no 'daily' data.")

    missing = [name for name in COLUMN_MAP if name not in daily]
    if missing:
        raise DataValidationError(f"Missing required field(s): {', '.join(missing)}.")

    lengths = {name: len(daily[name]) for name in COLUMN_MAP if isinstance(daily[name], list)}
    if len(lengths) != len(COLUMN_MAP) or len(set(lengths.values())) != 1:
        raise DataValidationError("Daily fields are not lists of equal length.")

    frame = pd.DataFrame({new: daily[old] for old, new in COLUMN_MAP.items()})
    if frame.empty:
        raise EmptyDatasetError("The API returned an empty dataset.")

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    total = len(frame)
    frame = frame.dropna(subset=["date", *NUMERIC_COLUMNS])
    inverted = frame["temp_min"] > frame["temp_max"]
    frame = frame[~inverted]
    dropped = total - len(frame)
    if dropped:
        logger.warning("Validation: dropped %d of %d rows (missing/invalid values)", dropped, total)
    if frame.empty:
        raise EmptyDatasetError("No valid records remained after validation.")

    frame = frame.sort_values("date").reset_index(drop=True)
    frame["temp_mean"] = (frame["temp_max"] + frame["temp_min"]) / 2
    return frame, dropped


def _current_conditions(raw: dict[str, Any]) -> dict[str, float]:
    block = raw.get("current")
    if not isinstance(block, dict):
        return {}
    current: dict[str, float] = {}
    for key in ("temperature_2m", "relative_humidity_2m", "wind_speed_10m"):
        try:
            current[key] = float(block[key])
        except (KeyError, TypeError, ValueError):
            logger.warning("Current conditions: field %s missing or invalid", key)
    return current


def _day(timestamp: pd.Timestamp) -> str:
    return timestamp.strftime("%a %d %b")


def temperature_trend(frame: pd.DataFrame) -> float:
    """Linear trend of the daily mean temperature in degrees per day."""
    if len(frame) < 3:
        return 0.0
    slope, _ = np.polyfit(np.arange(len(frame)), frame["temp_mean"].to_numpy(), 1)
    return float(slope)


def _trend_phrase(slope: float) -> str:
    if slope > TREND_THRESHOLD:
        return "a warming trend"
    if slope < -TREND_THRESHOLD:
        return "a cooling trend"
    return "stable temperatures"


def _build_metrics(frame: pd.DataFrame, units: dict[str, str]) -> list[Metric]:
    deg, mm, speed = units["temp"], units["precip"], units["wind"]
    hottest = frame.loc[frame["temp_max"].idxmax()]
    wettest = frame.loc[frame["precipitation"].idxmax()]
    windiest = frame.loc[frame["wind_max"].idxmax()]
    wet_days = int((frame["precipitation"] >= WET_DAY_MM).sum())
    return [
        Metric(
            "Average temperature",
            f"{frame['temp_mean'].mean():.1f}{deg}",
            f"Mean of daily highs and lows over {len(frame)} days",
        ),
        Metric("Peak temperature", f"{hottest['temp_max']:.1f}{deg}", f"Expected {_day(hottest['date'])}"),
        Metric(
            "Total precipitation",
            f"{frame['precipitation'].sum():.1f} {mm}",
            f"{wet_days} wet day(s); wettest {_day(wettest['date'])}",
        ),
        Metric("Maximum wind", f"{windiest['wind_max']:.1f} {speed}", f"Expected {_day(windiest['date'])}"),
    ]


def _build_insights(frame: pd.DataFrame, units: dict[str, str], slope: float) -> list[str]:
    deg, mm, speed = units["temp"], units["precip"], units["wind"]
    insights: list[str] = []

    warm = frame.loc[frame["temp_max"].idxmax()]
    cool = frame.loc[frame["temp_min"].idxmin()]
    insights.append(
        f"Warmest day is {_day(warm['date'])} with a high of {warm['temp_max']:.1f}{deg}; "
        f"the coolest night is {_day(cool['date'])} at {cool['temp_min']:.1f}{deg}."
    )

    direction = {"a warming trend": "rising", "a cooling trend": "falling"}.get(_trend_phrase(slope))
    if direction:
        insights.append(
            f"Daily mean temperature is {direction} by about {abs(slope):.1f}{deg} per day across the period."
        )
    else:
        insights.append(f"Daily mean temperature is stable (trend {slope:+.2f}{deg} per day).")

    wet = frame[frame["precipitation"] >= WET_DAY_MM]
    if wet.empty:
        insights.append(f"No significant rain (at least {WET_DAY_MM:g} {mm}) is expected during the period.")
    else:
        wettest = frame.loc[frame["precipitation"].idxmax()]
        insights.append(
            f"Rain is expected on {len(wet)} of {len(frame)} days; the wettest day is "
            f"{_day(wettest['date'])} with {wettest['precipitation']:.1f} {mm}."
        )

    windiest = frame.loc[frame["wind_max"].idxmax()]
    wind_text = f"Peak wind reaches {windiest['wind_max']:.1f} {speed} on {_day(windiest['date'])}"
    insights.append(
        wind_text + (" - strong wind, take care outdoors." if windiest["wind_max"] >= STRONG_WIND_KMH else ".")
    )

    if len(frame) >= 2:
        change = frame["temp_mean"].diff().iloc[1:]
        idx = change.abs().idxmax()
        insights.append(
            f"Largest day-to-day shift is {change[idx]:+.1f}{deg} between "
            f"{_day(frame.loc[idx - 1, 'date'])} and {_day(frame.loc[idx, 'date'])}."
        )
    return insights


def analyze(raw: dict[str, Any], *, location: str, source_url: str) -> AnalysisResult:
    """Clean ``raw`` API data and produce statistics, trends and insights."""
    frame, dropped = build_dataframe(raw)
    units_raw = raw.get("daily_units") if isinstance(raw.get("daily_units"), dict) else {}
    units = {
        "temp": units_raw.get("temperature_2m_max", "°C"),
        "precip": units_raw.get("precipitation_sum", "mm"),
        "wind": units_raw.get("wind_speed_10m_max", "km/h"),
    }
    slope = temperature_trend(frame)
    metrics = _build_metrics(frame, units)
    insights = _build_insights(frame, units, slope)

    deg = units["temp"]
    wet_days = int((frame["precipitation"] >= WET_DAY_MM).sum())
    rain_text = f"rain on {wet_days} day(s)" if wet_days else "no significant rain"
    summary = (
        f"Over the next {len(frame)} days in {location}, temperatures are expected to average "
        f"{frame['temp_mean'].mean():.1f}{deg} (lows near {frame['temp_min'].min():.1f}{deg}, "
        f"highs up to {frame['temp_max'].max():.1f}{deg}), with {_trend_phrase(slope)} and {rain_text}."
    )
    logger.info("Processing completed: %d rows analysed, %d insights", len(frame), len(insights))

    return AnalysisResult(
        location=location,
        latitude=float(raw.get("latitude", np.nan)),
        longitude=float(raw.get("longitude", np.nan)),
        timezone=str(raw.get("timezone", "unknown")),
        source_name=SOURCE_NAME,
        source_url=source_url,
        units=units,
        frame=frame,
        metrics=metrics,
        insights=insights,
        summary=summary,
        trend_per_day=slope,
        current=_current_conditions(raw),
        dropped_rows=dropped,
    )
