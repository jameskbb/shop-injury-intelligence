"""TOOL BURDEN BY YEAR heatmap (PAGE-01): the app's only heatmap with a time axis.

NEISS carries no incident timestamp, weekday or month -- `fact_cases` has a treatment `year` and
nothing finer -- so the honest temporal cross-tab is tool x year, never tool x hour-of-day. Rows
are tools (highest-burden first), columns are the available treatment years, and each cell is
that tool's estimated U.S. ER-treated injuries IN THAT YEAR.

Three deliberate choices, all of them honesty constraints rather than styling:

1. **One basis, always per-year.** A cell is a single year, so its only meaningful basis is the
   period total of that one-year period. The sidebar's Annual average / Period total toggle is
   NOT threaded through here: `wii.metrics.estimator` computes `annual_average` by dividing a
   group's estimate by the whole filter window's `n_years`, so an "annual average" tool x year
   cell would be that year's estimate divided by the 20-year span -- a number that means nothing.
   `wii.app.data.tool_year_matrix` therefore resolves every cell at `period_total`.

2. **Suppressed cells are unfilled AND glyph-marked** (the two-dimensional equivalent of
   `charts.trend`'s gapping, which never interpolates a line through a suppressed year). A
   suppressed cell gets `z=None`, so no color from the sequential scale is painted at all, plus
   an explicit "x" glyph from an overlay trace. The glyph is load-bearing: `SEQUENTIAL_SCALE`
   starts a hair off bare vellum, which is near-indistinguishable from the sheet, so "no fill"
   ALONE would read as "a measured low value". Limited-sample cells keep their fill and their
   value under a dashed construction ring -- line treatment, never a caution colour. Both
   overlays appear in the legend and both carry the same full hover text as the fill, so a
   suppressed cell can never hover a plausible number (`stability_badge.resolve` has already
   nulled its value and CI).

3. **Log-scaled color.** Tool burden spans ~1,200 to ~38,000 estimated injuries per cell; on a
   linear scale every tool outside the top few would render at the bottom stop and the chart
   would answer nothing about change over time. Color maps log10(estimate), floored at the CPSC
   reportability floor of 1,200 -- which costs nothing, because no cell below 1,200 is ever
   displayed (`est_lt_1200` hard-suppresses it). The colorbar is ticked with real estimate
   numbers, never log units.

Pure figure-builder: no Streamlit and no `wii.metrics` calls happen here. Every input row has
already been stability-resolved by `wii.app.data.tool_year_matrix`.
"""

from __future__ import annotations

import math

import plotly.graph_objects as go
import polars as pl

from wii.app import theme
from wii.app.charts.trend import SCHEMA_CHANGE_CAPTION, SCHEMA_CHANGE_YEAR
from wii.app.components.stability_badge import LIMITED_SAMPLE_LABEL, SUPPRESSED_LABEL
from wii.app.theme import CONSTRUCTION, GRAPHITE_SOFT, SEQUENTIAL_SCALE, chart_note

__all__ = [
    "SCHEMA_CHANGE_CAPTION",
    "SCHEMA_CHANGE_YEAR",
    "SUBTITLE",
    "TITLE",
    "build_temporal_heatmap",
    "limited_cell_count",
    "log10_floored",
    "suppressed_cell_count",
]

TITLE = "Tool burden by year"
SUBTITLE = (
    "Which tools' estimated ER burden grew, shrank or stayed flat across the record; one cell "
    "per tool per treatment year, always that single year's estimate. A cell with no reportable "
    "estimate is left unfilled and marked ×, never shaded as a measured low value; colour is "
    "log-scaled."
)
YEAR_RANGE_NOTE = (
    "The sidebar's year range does not scope this chart (the full record is always shown so a "
    "trend is never cropped); every other sidebar filter is applied."
)

# NEISS has no incident time-of-day, weekday or month field; `year` is the only temporal
# dimension in `fact_cases`, so this is the finest honest time axis available.
ESTIMATE_LABEL = "Estimated U.S. ER-treated injuries"
COLORBAR_TITLE = "Estimated injuries<br>per year (log)"
STABLE_LABEL = "Stable"

# The CPSC hard floor (`wii.metrics.stability.EST_LT_1200`): no displayed cell is ever below it,
# so the color scale may start there without hiding anything.
SCALE_FLOOR = 1_200.0

# Round ladder for the colorbar; only the ticks inside the rendered range are kept.
_TICK_LADDER: tuple[float, ...] = (1_200.0, 2_000.0, 3_000.0, 5_000.0, 10_000.0, 20_000.0, 40_000.0)

_STATE_SUPPRESSED = "suppressed"
_STATE_LIMITED = "limited"

# Input frame columns (one row per tool x year, ALL pre-resolved by `wii.app.data.tool_year_matrix`
# via `stability_badge.resolve` at the period_total basis): tool_id, tool_label, year, value,
# ci_low, ci_high, raw_n, state ("stable" | "limited" | "suppressed").


def log10_floored(value: float | None) -> float | None:
    """log10 of an estimate, floored at `SCALE_FLOOR` so the color axis starts at the CPSC
    reportability floor. `None`/non-positive -> `None` (an unfilled cell, never log10 of 0,
    which diverges to -inf and would silently paint the bottom of the scale)."""
    if value is None or value <= 0:
        return None
    return math.log10(max(value, SCALE_FLOOR))


def _state_label(state: str) -> str:
    if state == _STATE_SUPPRESSED:
        return SUPPRESSED_LABEL
    if state == _STATE_LIMITED:
        return LIMITED_SAMPLE_LABEL
    return STABLE_LABEL


def _hover_text(tool_label: str, year: int, row: dict | None) -> str:
    """Estimate + 95% CI + raw sample N with the standing "(not a national count)" caveat +
    the cell's stability state. A suppressed cell shows "—" for the estimate: the resolved row
    carries no value at all, so no plausible number can leak into a tooltip."""
    if row is None or row["state"] == _STATE_SUPPRESSED or row["value"] is None:
        raw_n = row["raw_n"] if row is not None else 0
        return (
            f"{tool_label}, {year}<br>{ESTIMATE_LABEL}: —"
            f"<br>{raw_n:,} sample cases (not a national count)"
            f"<br>{SUPPRESSED_LABEL}"
        )
    if row["ci_low"] is not None and row["ci_high"] is not None:
        ci_text = f"{row['ci_low']:,.0f}–{row['ci_high']:,.0f}"
    else:
        ci_text = "—"
    return (
        f"{tool_label}, {year}<br>{ESTIMATE_LABEL}: {row['value']:,.0f}"
        f"<br>95% CI: {ci_text}"
        f"<br>{row['raw_n']:,} sample cases (not a national count)"
        f"<br>{_state_label(row['state'])}"
    )


def _colorbar_ticks(zmin: float, zmax: float) -> tuple[list[float], list[str]]:
    """Real-estimate tick labels on the log color axis (never log units). Always keeps at least
    the two endpoints so the colorbar can never render tickless."""
    values = [v for v in _TICK_LADDER if zmin <= math.log10(v) <= zmax]
    if len(values) < 2:
        values = [10**zmin, 10**zmax]
    return [math.log10(v) for v in values], [f"{v:,.0f}" for v in values]


def suppressed_cell_count(df: pl.DataFrame) -> int:
    """Cells with no reportable estimate -- surfaced in the page caption so the count of blanks
    is stated rather than left for the reader to tally off the grid."""
    if df.height == 0:
        return 0
    return int((df["state"] == _STATE_SUPPRESSED).sum())


def limited_cell_count(df: pl.DataFrame) -> int:
    if df.height == 0:
        return 0
    return int((df["state"] == _STATE_LIMITED).sum())


def _overlay(x: list[int], y: list[str], hover: list[str], *, name: str, symbol: str, color: str):
    return go.Scatter(
        x=x,
        y=y,
        mode="markers",
        name=name,
        # Both `color` and `line.color` are set to the same token: Plotly strokes an "-open"
        # symbol from `marker.color` in some builds and `marker.line.color` in others, and a
        # glyph that silently fails to draw would turn a suppressed cell back into an ambiguous
        # dark square. "-open" symbols never fill, so this stays an outline either way.
        marker={
            "symbol": symbol,
            "size": 9,
            "color": color,
            "line": {"color": color, "width": 1.6},
        },
        hovertext=hover,
        hoverinfo="text",
        showlegend=True,
    )


def build_temporal_heatmap(df: pl.DataFrame) -> go.Figure:
    """Rows = tools in the frame's own first-seen `tool_id` order (already burden-ranked by
    `wii.app.data.tool_year_matrix`), columns = every year present in the frame, ascending.

    The x axis is NUMERIC (not categorical) on purpose: it lets the 2019 NEISS coding boundary be
    drawn at x=2018.5, exactly on the cell edge between the two coding regimes, the same boundary
    `charts.trend` marks on its own year axis. The boundary is marked HERE and not only on the
    line chart because this chart's tool rows are the thing the 2019 change touches most: tool
    identity for narrative-refined tools is recovered from the narrative, and 2019 expanded
    narratives from 142 to 400 characters, so a row can step up at 2019 for coding reasons rather
    than injury reasons. Reading a row left-to-right across that line is exactly the mistake the
    marker exists to prevent.
    """
    fig = go.Figure()
    if df.height == 0:
        return fig

    tool_order = list(dict.fromkeys(df["tool_id"].to_list()))
    tool_labels = dict(zip(df["tool_id"], df["tool_label"], strict=True))
    years = sorted({int(y) for y in df["year"].to_list()})
    y_labels = [tool_labels.get(tid, tid) for tid in tool_order]

    z: list[list[float | None]] = []
    hover: list[list[str]] = []
    limited_x: list[int] = []
    limited_y: list[str] = []
    limited_hover: list[str] = []
    suppressed_x: list[int] = []
    suppressed_y: list[str] = []
    suppressed_hover: list[str] = []

    for tid in tool_order:
        label = tool_labels.get(tid, tid)
        by_year = {int(r["year"]): r for r in df.filter(pl.col("tool_id") == tid).to_dicts()}
        z_row: list[float | None] = []
        hover_row: list[str] = []
        for year in years:
            row = by_year.get(year)
            text = _hover_text(label, year, row)
            hover_row.append(text)
            if row is None or row["state"] == _STATE_SUPPRESSED or row["value"] is None:
                z_row.append(None)
                suppressed_x.append(year)
                suppressed_y.append(label)
                suppressed_hover.append(text)
                continue
            z_row.append(log10_floored(row["value"]))
            if row["state"] == _STATE_LIMITED:
                limited_x.append(year)
                limited_y.append(label)
                limited_hover.append(text)
        z.append(z_row)
        hover.append(hover_row)

    filled = [v for z_row in z for v in z_row if v is not None]
    zmin = math.log10(SCALE_FLOOR)
    zmax = max(filled) if filled else zmin + 1.0
    if zmax <= zmin:
        zmax = zmin + 1.0
    tickvals, ticktext = _colorbar_ticks(zmin, zmax)

    fig.add_trace(
        go.Heatmap(
            z=z,
            x=years,
            y=y_labels,
            colorscale=SEQUENTIAL_SCALE,
            zmin=zmin,
            zmax=zmax,
            hovertext=hover,
            hoverinfo="text",
            xgap=1,
            ygap=1,
            colorbar={
                "title": COLORBAR_TITLE,
                "tickvals": tickvals,
                "ticktext": ticktext,
                "thickness": 10,
                "outlinewidth": 0,
                "len": 0.9,
            },
        )
    )

    if limited_x:
        fig.add_trace(
            _overlay(
                limited_x,
                limited_y,
                limited_hover,
                name=LIMITED_SAMPLE_LABEL,
                symbol="circle-open",
                color=CONSTRUCTION,
            )
        )
    if suppressed_x:
        fig.add_trace(
            _overlay(
                suppressed_x,
                suppressed_y,
                suppressed_hover,
                name=SUPPRESSED_LABEL,
                symbol="x-thin",
                color=GRAPHITE_SOFT,
            )
        )

    fig.update_xaxes(
        title="Treatment year",
        dtick=1,
        range=[min(years) - 0.5, max(years) + 0.5],
        showgrid=False,
    )
    fig.update_yaxes(autorange="reversed", showgrid=False)
    # style_chart sets automargin, so the longest tool label ("Portable Circular Power Saw")
    # grows the left margin instead of being clipped to "...ortable Circular Power Saw". Height
    # tracks the row count so every tool keeps a readable band.
    theme.style_chart(fig, height=max(320, 26 * len(tool_order) + 140), grid="none", legend=True)

    if min(years) < SCHEMA_CHANGE_YEAR <= max(years):
        fig.add_vline(x=SCHEMA_CHANGE_YEAR - 0.5, line_dash="dot", line_color=CONSTRUCTION)
        fig.add_annotation(
            **chart_note(
                "2019 coding change",
                x=SCHEMA_CHANGE_YEAR - 0.5,
                xanchor="left",
                y=1.10,
                yref="paper",
                yanchor="bottom",
            )
        )
    return fig
