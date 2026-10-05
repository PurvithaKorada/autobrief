"""Chart rendering (PNG bytes) shared by the HTML and PDF reports."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # headless backend: no display needed (servers, schedulers)
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from autobrief.processing.analyzer import AnalysisResult  # noqa: E402

NAVY = "#0B1F3A"
TEAL = "#0E9AA7"
ORANGE = "#E8743B"
BLUE = "#3B82C4"
GRID = "#E2E8F0"


def _finish(fig: plt.Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


def _style_axes(ax: plt.Axes) -> None:
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#94A3B8")
    ax.tick_params(colors="#475569", labelsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d"))


def temperature_chart(analysis: AnalysisResult) -> bytes:
    """Daily high/low temperature band with the mean line."""
    frame, unit = analysis.frame, analysis.units["temp"]
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    ax.fill_between(frame["date"], frame["temp_min"], frame["temp_max"], color=TEAL, alpha=0.18, label="Daily range")
    ax.plot(frame["date"], frame["temp_max"], color=ORANGE, marker="o", markersize=4, linewidth=2, label="High")
    ax.plot(frame["date"], frame["temp_min"], color=BLUE, marker="o", markersize=4, linewidth=2, label="Low")
    ax.plot(frame["date"], frame["temp_mean"], color=NAVY, linestyle="--", linewidth=1.2, label="Mean")
    ax.set_ylabel(f"Temperature ({unit})", fontsize=8, color="#475569")
    ax.set_title("Temperature outlook", loc="left", fontsize=10, fontweight="bold", color=NAVY)
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    _style_axes(ax)
    return _finish(fig)


def precipitation_chart(analysis: AnalysisResult) -> bytes:
    """Daily precipitation bars with wind speed overlaid as a line."""
    frame = analysis.frame
    fig, ax = plt.subplots(figsize=(7.2, 2.8))
    ax.bar(frame["date"], frame["precipitation"], color=BLUE, width=0.55, label="Precipitation")
    ax.set_ylabel(f"Precipitation ({analysis.units['precip']})", fontsize=8, color="#475569")
    ax.set_title("Precipitation and wind", loc="left", fontsize=10, fontweight="bold", color=NAVY)
    wind_ax = ax.twinx()
    wind_ax.plot(
        frame["date"], frame["wind_max"], color=ORANGE, marker="s", markersize=3.5, linewidth=1.6, label="Max wind"
    )
    wind_ax.set_ylabel(f"Wind ({analysis.units['wind']})", fontsize=8, color="#475569")
    wind_ax.tick_params(colors="#475569", labelsize=8)
    wind_ax.spines["top"].set_visible(False)
    handles = ax.get_legend_handles_labels()[0] + wind_ax.get_legend_handles_labels()[0]
    labels = ax.get_legend_handles_labels()[1] + wind_ax.get_legend_handles_labels()[1]
    ax.legend(handles, labels, frameon=False, fontsize=8, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    _style_axes(ax)
    return _finish(fig)
