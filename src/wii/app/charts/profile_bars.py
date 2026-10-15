"""The app's ONE horizontal share-bar builder, plus the disposition strip.

`build_share_bars` draws every "share of cases by category" view in the product -- the diagnosis
profile, the mechanism profile, the Overview injury mix, My Shop's top diagnoses and mechanisms,
the Body Map's region detail and the Mechanisms page's own mechanism shares. The review found the
same fact drawn on five pages in two orientations, so a reader re-learned the chart per page; it
is one horizontal form now, with one set of rules:

  * suppressed categories are DROPPED, never drawn as a short bar or a zero (the page names them);
  * a limited category keeps its length, is filled one step lighter and carries the dashed
    construction outline (`charts.marks`) -- confidence is line treatment, never colour;
  * the value is lettered past the end of the bar, so the axis is context rather than the only
    way to read a number;
  * an optional per-bar `baseline` column draws a workshop-baseline tick across the bar, which is
    what lets "74.6% laceration" be read against the workshop's own share without a second chart;
  * the value axis range is always explicit, so Plotly can never autorange an empty domain.

Category colour comes from a fixed semantic palette looked up BY NAME (`theme.DIAGNOSIS_COLORS` /
`theme.MECHANISM_COLORS`) -- never a rotating colorway, so a diagnosis keeps its ink on every
page. Pure figure-builders: no Streamlit and no `wii.metrics` calls happen in this module.
"""

from __future__ import annotations

from collections.abc import Mapping

import plotly.graph_objects as go
import polars as pl

from wii.app.charts import marks
from wii.app.theme import (
    CHART_DATA_LABEL_SIZE,
    DIAGNOSIS_COLORS,
    FONT_NARROW,
    FONT_SANS,
    GRAPHITE,
    GRAPHITE_SOFT,
    MECHANISM_COLORS,
    SEQUENTIAL_SCALE,
    SEVERE,
    TYPE_LABEL,
    chart_note,
    style_chart,
)

DIAGNOSIS_SUBTITLE = "Within-tool shares of the primary diagnosis category."
MECHANISM_SUBTITLE = "Within-tool shares of the top injury mechanisms recorded in ER narratives."
DISPOSITION_CAPTION = (
    "Shown as a single 100%-stacked bar -- this is not a sequential process/funnel; each case "
    "has exactly one recorded disposition."
)
BASELINE_NOTE = "The tick on each bar is the all-workshop share for that category."
EMPTY_SHARE_BARS_NOTE = (
    "No category in this view clears the reporting floor, so no shares are drawn."
)

_STATE_ORDER = {"stable": 0, "limited": 1, "suppressed": 2}

#: Vertical pixels per category row and the fixed chrome around them -- the same row-height rule
#: `ranked_bars` uses, so a 4-row profile is not stretched to Plotly's default 450px and a 13-row
#: one is not crushed into it.
ROW_HEIGHT = 26
_CHROME_HEIGHT = 56
_MIN_HEIGHT = 140

#: Headroom past the longest bar so the lettered value sits inside the explicit range.
_X_HEADROOM = 1.22

# Only admitted/fatality may use the severe ink -- the remaining buckets reuse theme sequential/
# muted tokens, never a new hex literal.
_DISPOSITION_COLORS: dict[str, str] = {
    "released": SEQUENTIAL_SCALE[2],
    "transferred": SEQUENTIAL_SCALE[3],
    "observation": SEQUENTIAL_SCALE[1],
    "left_without_treatment": GRAPHITE_SOFT,
    "admitted": SEVERE,
    "fatality": SEVERE,
}

#: A disposition segment at or above this share is lettered directly under the strip; anything
#: thinner would collide with its neighbour, so it keeps a legend key instead.
DISPOSITION_LABEL_FLOOR = 0.03
_DISPOSITION_HEIGHT = 104

# Share-bar input frame columns (one row per category, pre-resolved via
# `stability_badge.resolve_share`): category, label, value (share, 0-1, unrounded), ci_low,
# ci_high, raw_n, state. An optional `baseline` column (share, 0-1, or null) adds the tick.


def _hover(row: dict, value_label: str) -> str:
    state_note = "" if row["state"] == "stable" else " (limited sample)"
    text = f"{row['label']}<br>{value_label}: {marks.fmt_share(row['value'])}{state_note}"
    baseline = row.get("baseline")
    if baseline is not None:
        text = f"{text}<br>All-workshop share: {marks.fmt_share(baseline)}"
    return f"{text}<br>{row['raw_n']:,} sample cases (not a national count)"


def drawable_rows(df: pl.DataFrame) -> list[dict]:
    """The rows `build_share_bars` would draw: suppressed categories dropped, stable block first,
    each block ordered by share descending. A page can ask for this to decide between the chart
    and one honest line, without re-deriving the rule."""
    if df.height == 0:
        return []
    rows = df.filter(pl.col("state") != "suppressed").to_dicts()
    rows.sort(key=lambda r: (_STATE_ORDER[r["state"]], -(r["value"] or 0.0)))
    return [r for r in rows if r["value"] is not None]


def build_share_bars(
    df: pl.DataFrame,
    palette: Mapping[str, str],
    *,
    value_label: str = "Share of cases",
    max_rows: int | None = None,
    drop_when_empty: bool = False,
) -> go.Figure | None:
    """One horizontal share bar per category, in `palette`'s ink, ordered stable-first.

    `drop_when_empty=True` returns `None` when nothing is drawable so the page can print one
    honest line instead of an empty grid; the default returns the (empty) figure, which keeps
    every existing call site total.
    """
    rows = drawable_rows(df)
    if max_rows is not None:
        rows = rows[:max_rows]
    if not rows and drop_when_empty:
        return None

    labels = [r["label"] for r in rows]
    values = [r["value"] for r in rows]
    states = [r["state"] for r in rows]
    colors = [
        marks.fill_for(palette.get(r["category"], palette.get("other", GRAPHITE_SOFT)), r["state"])
        for r in rows
    ]
    outline_widths = [1 if state == "stable" else 0 for state in states]
    baselines = [r.get("baseline") for r in rows]

    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            # The number is lettered past the end of the bar: the axis stays context, and a short
            # bar never has to be traced back to it.
            text=[f" {marks.fmt_share(v)}" for v in values],
            textposition="outside",
            cliponaxis=False,
            textfont={"family": FONT_NARROW, "size": CHART_DATA_LABEL_SIZE, "color": GRAPHITE},
            marker={"color": colors, "line": {"color": GRAPHITE, "width": outline_widths}},
            hovertext=[_hover(r, value_label) for r in rows],
            hoverinfo="text",
        )
    )
    fig.update_layout(bargap=0.3)
    marks.add_confidence_outlines(fig, values=values, states=states)
    if any(b is not None for b in baselines):
        marks.add_baseline_ticks(fig, baselines=baselines)

    style_chart(
        fig,
        height=max(_MIN_HEIGHT, ROW_HEIGHT * max(len(rows), 1) + _CHROME_HEIGHT),
        margin={"l": 8, "r": 56, "t": 8, "b": 36},
        grid="x",
    )
    x_max = max([v for v in values if v is not None], default=0.0)
    fig.update_xaxes(
        title_text=value_label,
        tickformat=".0%",
        title_standoff=8,
        nticks=5,
        range=[0, min(1.0, (x_max or 0.2) * _X_HEADROOM)],
    )
    fig.update_yaxes(
        autorange="reversed",
        tickfont={"size": TYPE_LABEL, "color": GRAPHITE},
        ticklabelstandoff=8,
    )
    if any(state != "stable" for state in states):
        fig.add_annotation(
            **chart_note(
                "Dashed outline: limited sample",
                xref="paper",
                x=1.0,
                xanchor="right",
                yref="paper",
                y=1.0,
                yanchor="bottom",
            )
        )
    return fig


def build_diagnosis_bars(df: pl.DataFrame, **kwargs) -> go.Figure:
    """The diagnosis profile, on the shared horizontal builder. Always returns a figure, so no
    existing page call site has to learn a new contract."""
    return build_share_bars(
        df, DIAGNOSIS_COLORS, value_label="Share of primary diagnosis", **kwargs
    )


def build_mechanism_bars(df: pl.DataFrame, **kwargs) -> go.Figure:
    """The mechanism profile, on the shared horizontal builder."""
    return build_share_bars(
        df, MECHANISM_COLORS, value_label="Share of cases (narrative-derived)", **kwargs
    )


def disposition_summary_line(rows: list[dict]) -> str:
    """The drawn disposition segments as one readable line ("Released 92.4% · Transferred 4.8%").
    A part-to-whole dominated by one class is a number before it is a chart, so the strip is drawn
    with this line rather than instead of it."""
    parts = [
        f"{row['label']} {marks.fmt_share(row['value'])}"
        for row in rows
        if row.get("value") is not None and row["state"] != "suppressed"
    ]
    return " · ".join(parts)


def build_disposition_stack(rows: list[dict]) -> go.Figure:
    """A thin 100%-stacked strip of where the ER cases went (TOOL-14).

    `rows`: one dict per disposition bucket, already basis-resolved via
    `stability_badge.resolve_share` -- {metric_id, label, value (0-1 share or None), state,
    raw_n}. A suppressed segment is OMITTED entirely rather than drawn as a misleading
    zero-width bar. Segments at or above `DISPOSITION_LABEL_FLOOR` are lettered directly under
    the strip; thinner drawn segments keep a legend key, so nothing on the bar is unnamed and
    nothing in the legend is absent from the bar.
    """
    fig = go.Figure()
    drawn = [r for r in rows if r.get("value") is not None and r["state"] != "suppressed"]
    cursor = 0.0
    labelled: list[tuple[float, str]] = []
    any_legend = False
    for row in drawn:
        color = _DISPOSITION_COLORS.get(row["metric_id"], GRAPHITE_SOFT)
        state_note = "" if row["state"] == "stable" else " (limited sample)"
        big_enough = row["value"] >= DISPOSITION_LABEL_FLOOR
        any_legend = any_legend or not big_enough
        fig.add_trace(
            go.Bar(
                x=[row["value"]],
                y=[""],
                orientation="h",
                name=row["label"],
                showlegend=not big_enough,
                marker={
                    "color": marks.fill_for(color, row["state"]),
                    "line": {
                        "color": GRAPHITE,
                        "width": 1 if row["state"] == "stable" else 0,
                    },
                },
                hovertext=[
                    f"{row['label']}: {marks.fmt_share(row['value'])}{state_note}"
                    f"<br>{row['raw_n']:,} sample cases (not a national count)"
                ],
                hoverinfo="text",
            )
        )
        if big_enough:
            labelled.append(
                (cursor + row["value"] / 2, f"{row['label']} {marks.fmt_share(row['value'])}")
            )
        cursor += row["value"]

    for x, text in labelled:
        fig.add_annotation(
            text=text,
            x=x,
            xref="x",
            xanchor="center",
            y=0,
            yref="paper",
            yanchor="top",
            showarrow=False,
            font={"family": FONT_SANS, "size": CHART_DATA_LABEL_SIZE, "color": GRAPHITE},
        )

    fig.update_layout(barmode="stack", bargap=0.72)
    style_chart(
        fig,
        height=_DISPOSITION_HEIGHT,
        margin={"l": 8, "r": 8, "t": 8 if not any_legend else 28, "b": 40},
        grid="none",
        legend=any_legend,
    )
    fig.update_xaxes(
        title_text="Share of known-disposition cases",
        tickformat=".0%",
        range=[0, 1],
        showticklabels=False,
        title_standoff=24,
    )
    fig.update_yaxes(showticklabels=False)
    return fig
