import pytest

from autobrief.exceptions import DataValidationError, EmptyDatasetError
from autobrief.processing.analyzer import analyze, build_dataframe, temperature_trend


def run(payload):
    return analyze(payload, location="Testville", source_url="https://example.test")


def metric(result, label):
    return next(m for m in result.metrics if m.label == label)


def test_metrics_match_hand_calculated_values(raw_payload):
    result = run(raw_payload)

    assert metric(result, "Average temperature").value == "13.4°C"  # (12+13.5+15+17+15+11+10)/7
    assert metric(result, "Peak temperature").value == "22.0°C"
    assert "Wed 07 Oct" in metric(result, "Peak temperature").detail
    assert metric(result, "Total precipitation").value == "16.9 mm"
    assert "4 wet day(s)" in metric(result, "Total precipitation").detail
    assert "Thu 08 Oct" in metric(result, "Total precipitation").detail
    assert metric(result, "Maximum wind").value == "45.0 km/h"
    assert "Tue 06 Oct" in metric(result, "Maximum wind").detail


def test_dataframe_is_typed_sorted_and_has_mean_column(raw_payload):
    raw_payload["daily"] = {k: list(reversed(v)) for k, v in raw_payload["daily"].items()}  # unsorted input

    frame, dropped = build_dataframe(raw_payload)

    assert dropped == 0
    assert frame["date"].is_monotonic_increasing
    assert str(frame["date"].dtype).startswith("datetime64")
    assert frame.loc[0, "temp_mean"] == 12.0


def test_trend_is_exact_for_linear_data():
    payload = {
        "daily": {
            "time": ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"],
            "temperature_2m_max": [12, 14, 16, 18],
            "temperature_2m_min": [8, 10, 12, 14],
            "precipitation_sum": [0] * 4,
            "wind_speed_10m_max": [10] * 4,
        }
    }
    result = run(payload)

    assert result.trend_per_day == pytest.approx(2.0)
    assert "warming trend" in result.summary
    assert any("rising by about 2.0" in i for i in result.insights)


def test_flat_temperatures_are_reported_stable(raw_payload):
    raw_payload["daily"]["temperature_2m_max"] = [15] * 7
    raw_payload["daily"]["temperature_2m_min"] = [9] * 7

    result = run(raw_payload)

    assert abs(result.trend_per_day) < 1e-9
    assert "stable temperatures" in result.summary


def test_trend_needs_at_least_three_points(raw_payload):
    frame, _ = build_dataframe(raw_payload)
    assert temperature_trend(frame.head(2)) == 0.0


def test_rows_with_missing_or_invalid_values_are_dropped(raw_payload):
    raw_payload["daily"]["temperature_2m_max"][1] = None  # missing
    raw_payload["daily"]["precipitation_sum"][2] = "n/a"  # non-numeric
    raw_payload["daily"]["temperature_2m_min"][3] = 99  # min > max is impossible

    result = run(raw_payload)

    assert result.dropped_rows == 3
    assert len(result.frame) == 4


def test_missing_required_field_raises_with_field_name(raw_payload):
    del raw_payload["daily"]["wind_speed_10m_max"]
    with pytest.raises(DataValidationError, match="wind_speed_10m_max"):
        run(raw_payload)


def test_unequal_field_lengths_raise(raw_payload):
    raw_payload["daily"]["precipitation_sum"].pop()
    with pytest.raises(DataValidationError, match="equal length"):
        run(raw_payload)


def test_empty_dataset_raises(raw_payload):
    raw_payload["daily"] = {k: [] for k in raw_payload["daily"]}
    with pytest.raises(EmptyDatasetError):
        run(raw_payload)


def test_all_rows_invalid_raises_empty_dataset(raw_payload):
    raw_payload["daily"]["temperature_2m_max"] = [None] * 7
    with pytest.raises(EmptyDatasetError):
        run(raw_payload)


def test_current_conditions_parsed_and_optional(raw_payload):
    assert run(raw_payload).current == {"temperature_2m": 14.2, "relative_humidity_2m": 71.0, "wind_speed_10m": 12.0}
    del raw_payload["current"]
    assert run(raw_payload).current == {}


def test_strong_wind_is_flagged_only_above_threshold(raw_payload):
    assert any("strong wind" in i for i in run(raw_payload).insights)
    raw_payload["daily"]["wind_speed_10m_max"] = [15, 20, 39, 10, 22, 30, 18]
    assert not any("strong wind" in i for i in run(raw_payload).insights)


def test_dry_period_is_described(raw_payload):
    raw_payload["daily"]["precipitation_sum"] = [0, 0.2, 0, 0.5, 0, 0, 0]
    result = run(raw_payload)
    assert any("No significant rain" in i for i in result.insights)
    assert "no significant rain" in result.summary


def test_units_come_from_the_payload(raw_payload):
    raw_payload["daily_units"] = {"temperature_2m_max": "°F", "precipitation_sum": "inch", "wind_speed_10m_max": "mph"}
    result = run(raw_payload)
    assert metric(result, "Average temperature").value.endswith("°F")
    assert metric(result, "Maximum wind").value.endswith("mph")
