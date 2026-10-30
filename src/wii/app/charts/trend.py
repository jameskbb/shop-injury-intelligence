"""INJURY TREND (PAGE-02, D-10) and MECHANISM TREND (PAGE-04, D-13): per-year estimate/share with
a basis-matched 95% CI band. Stability-honest -- limited years/points render hollow markers,
suppressed years/points are gapped (never interpolated through), with a caption noting the 2019
NEISS coding-schema change. Pure figure-builders -- `build_trend`/`build_share_trend` take an
already-resolved per-year Polars frame; no Streamlit/wii.metrics calls happen in this module.
`build_share_trend` never re-applies the D-13 consistency gate itself -- the frame it receives
(`wii.app.data.mechanism_trend_frame`) already carries only gate-passing years.
"""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl

from wii.app.theme import (
    CONSTRUCTION,
    GRAPHITE_SOFT,
    MECHANISM_COLORS,
    PRIMARY,
    SEQUENTIAL_SCALE,
    chart_note,
    style_chart,
)

#: The y axis title never changes with the sidebar basis: every point on this chart is ONE
#: treatment year, so "annual average" would relabel a per-year series and invite exactly the
#: misreading the temporal heatmap documents and avoids.
Y_AXIS_TITLE = "Estimated injuries per year"

SUBTITLE = (
    "Per-year estimates -- limited-sample years render hollow, suppressed years are gapped "
    "rather than smoothed or interpolated through."
)
#: Direction is stated in words, never carried by colour. Every metric in this product counts
#: injuries, so a falling line is the favourable direction -- the opposite of the "up is good"
#: convention a reader brings from most dashboards. The line itself stays one neutral ink, so a
#: rise is never coloured as an alarm or a fall as a success.
DIRECTION_NOTE = (
    "Fewer estimated injuries is the lower-burden direction; the line is drawn in one ink either "
    "way, so a rise or a fall is never coloured as good or bad news."
)
SCHEMA_CHANGE_YEAR = 2019
SCHEMA_CHANGE_CAPTION = (
    "In 2019, NEISS added secondary body-part/diagnosis fields and expanded narrative length "
    "from 142 to 400 characters -- years before and after are not perfectly comparable."
)
MECHANISM_TREND_PRE2019_NOTE = (
    "Shown for 2019–{latest} only: earlier narratives are truncated to 142 characters, which "
    "lowers classification confidence and would misrepresent year-over-year change."
)

# Input frame columns (one row per year, ALL pre-resolved by the caller via stability_badge):
# year, value, ci_low, ci_high, raw_n, state ("stable" | "limited" | "suppressed").


def _hover_text(row: dict, basis_label: str) -> str:
    state_note = "" if row["state"] == "stable" else f" ({row['state']})"
    if row["state"] == "suppressed":
        return f"{row['year']}<br>{basis_label}: suppressed{state_note}"
    return (
        f"{row['year']}<br>{basis_label}: {row['value']:,.0f}{state_note}"
        f"<br>{row['raw_n']:,} sample cases (not a national count)"
    )


def build_trend(df: pl.DataFrame, basis: str) -> go.Figure:
    df = df.sort("year")
    rows = list(df.iter_rows(named=True))
    years = [r["year"] for r in rows]
    values = [None if r["state"] == "suppressed" else r["value"] for r in rows]
    ci_low = [None if r["state"] == "suppressed" else r["ci_low"] for r in rows]
    ci_high = [None if r["state"] == "suppressed" else r["ci_high"] for r in rows]
    symbols = ["circle" if r["state"] == "stable" else "circle-open" for r in rows]
    basis_label = (
        "Estimated annual average" if basis == "annual_average" else "Estimated period total"
    )
    hover = [_hover_text(r, basis_label) for r in rows]

    fig = go.Figure()
    if any(v is not None for v in ci_high):
        fig.add_trace(
            go.Scatter(
                x=years,
                y=ci_high,
                mode="lines",
                line={"width": 0},
                hoverinfo="skip",
                showlegend=False,
                connectgaps=False,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=years,
                y=ci_low,
                mode="lines",
                line={"width": 0},
                fill="tonexty",
                # The band is the palest stop of the sequential ramp -- ink density, not a new
                # literal, and it stays a whole step below the line it surrounds.
                fillcolor=SEQUENTIAL_SCALE[0],
                hoverinfo="skip",
                showlegend=False,
                connectgaps=False,
            )
        )

    fig.add_trace(
        go.Scatter(
            x=years,
            y=values,
            mode="lines+markers",
            name=basis_label,
            line={"color": PRIMARY},
            marker={"symbol": symbols, "size": 8, "color": PRIMARY},
            connectgaps=False,
            hovertext=hover,
            hoverinfo="text",
        )
    )
    # Y from 0: the series otherwise starts at the 1,200 suppression floor, which turns an
    # ordinary year-on-year wobble into an apparent 60% swing.
    top = max((v for v in ci_high if v is not None), default=None)
    plotted = max((v for v in values if v is not None), default=0.0) or 0.0
    fig.update_yaxes(title=Y_AXIS_TITLE, rangemode="tozero", range=[0, (top or plotted) * 1.08])
    fig.update_xaxes(title="Year", dtick=1)
    style_chart(fig, height=300, margin={"l": 8, "r": 16, "t": 24, "b": 8}, grid="y")

    if years and min(years) < SCHEMA_CHANGE_YEAR <= max(years):
        fig.add_vline(x=SCHEMA_CHANGE_YEAR, line_dash="dot", line_color=CONSTRUCTION)
        fig.add_annotation(
            **chart_note(
                "2019 coding change",
                x=SCHEMA_CHANGE_YEAR,
                xanchor="left",
                y=1.0,
                yref="paper",
                yanchor="bottom",
            )
        )
    return fig


# Mechanism trend input frame columns (one row per gate-passing year x top-N-STABLE mechanism,
# ALL pre-resolved by the caller via stability_badge, D-13): year, mechanism, label, value,
# ci_low, ci_high, raw_n, state ("stable" | "limited" | "suppressed").


def _fmt_pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "Not reportable"


def _mechanism_hover_text(year: int, label: str, row: dict | None) -> str:
    if row is None or row["state"] == "suppressed" or row["value"] is None:
        return f"{year}<br>{label}: suppressed"
    state_note = "" if row["state"] == "stable" else f" ({row['state']})"
    ci_text = (
        f"{_fmt_pct(row['ci_low'])}–{_fmt_pct(row['ci_high'])}"
        if row["ci_low"] is not None and row["ci_high"] is not None
        else "Not reportable"
    )
    return (
        f"{year}<br>{label}: {_fmt_pct(row['value'])}{state_note}"
        f"<br>95% CI: {ci_text}"
        f"<br>{row['raw_n']:,} sample cases (not a national count)"
    )


def build_share_trend(df: pl.DataFrame) -> go.Figure:
    """MECHANISM TREND (D-13): one line per mechanism, colored from `theme.MECHANISM_COLORS`,
    over the already-gated `year`s in `df` (`wii.app.data.mechanism_trend_frame`'s own
    passing-years-only frame). Suppressed points render `y=None` (gapped, `connectgaps=False`,
    same convention as `build_trend`); limited points keep their value but use a hollow
    ("circle-open") marker. The x range is restricted to exactly the years present in `df` --
    D-13's gate has already decided which years belong in the window, so the axis must never
    imply a wider span than that.
    """
    fig = go.Figure()
    if df.height == 0:
        return fig

    years = sorted(set(df["year"].to_list()))
    mechanisms = list(dict.fromkeys(df["mechanism"].to_list()))
    for mechanism in mechanisms:
        mech_rows = {r["year"]: r for r in df.filter(pl.col("mechanism") == mechanism).to_dicts()}
        label = next((r["label"] for r in mech_rows.values()), mechanism)
        y_values: list[float | None] = []
        symbols: list[str] = []
        hover: list[str] = []
        for year in years:
            row = mech_rows.get(year)
            if row is None or row["state"] == "suppressed" or row["value"] is None:
                y_values.append(None)
                symbols.append("circle")
            else:
                y_values.append(row["value"])
                symbols.append("circle-open" if row["state"] == "limited" else "circle")
            hover.append(_mechanism_hover_text(year, label, row))
        color = MECHANISM_COLORS.get(mechanism, GRAPHITE_SOFT)
        fig.add_trace(
            go.Scatter(
                x=years,
                y=y_values,
                mode="lines+markers",
                name=label,
                line={"color": color},
                marker={"symbol": symbols, "size": 8, "color": color},
                connectgaps=False,
                hovertext=hover,
                hoverinfo="text",
            )
        )
    fig.update_yaxes(
        title="Share of cases (narrative-derived)", tickformat=".0%", rangemode="tozero"
    )
    fig.update_xaxes(title="Year", dtick=1, range=[min(years) - 0.5, max(years) + 0.5])
    style_chart(fig, height=340, margin={"l": 8, "r": 16, "t": 8, "b": 8}, grid="y", legend=True)
    return fig
