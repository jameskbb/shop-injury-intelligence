"""TOOL INJURY LANDSCAPE (PAGE-01, D-09): a log-x burden-vs-severity bubble chart. Pure
figure-builder -- `build_landscape` takes an already-resolved per-tool Polars frame (built by
`wii.app.data.landscape_frame`, which does the `stability_badge.resolve`/`resolve_share` work)
and returns a `go.Figure`; no Streamlit calls happen in this module.
"""

from __future__ import annotations

import math

import plotly.graph_objects as go
import polars as pl

from wii.app.theme import (
    CONSTRUCTION,
    FAMILY_COLORS,
    GRAPHITE,
    GRAPHITE_SOFT,
    RULE,
    VELLUM,
    chart_note,
    style_chart,
)

SUBTITLE = (
    "High national injury burden does not necessarily correspond to high severity among "
    "ER-treated cases. Bubble area is the number of sample cases behind the point, colour is the "
    "tool family, and the dotted lines mark the median tool on each axis."
)
CAPTION_X = "Higher reported burden"
CAPTION_Y = "Higher severity among ER cases"

#: How many points get a printed name. Burden leaders answer the page title's question; the two
#: severity extremes answer the one the y axis poses. Labelling every point at 31 tools produces
#: an unreadable thicket, and labelling none -- which is what shipped -- means the landing page's
#: primary visual cannot be read at all without a mouse, and not at all on a phone.
LABEL_TOP_BURDEN = 6
LABEL_SEVERITY_EXTREMES = 2

# Input frame columns (one row per tool, ALL pre-resolved by the caller via stability_badge):
# tool_id, label, family_id, estimate, estimate_ci_low, estimate_ci_high, estimate_raw_n,
# estimate_state, escalated_share, escalated_ci_low, escalated_ci_high, escalated_raw_n,
# escalated_state -- state in {"stable", "limited", "suppressed"}.


def _hover_text(row: dict, basis_label: str) -> str:
    estimate_note = "" if row["estimate_state"] == "stable" else " (limited sample)"
    escalated_note = "" if row["escalated_state"] == "stable" else " (limited sample)"
    return (
        f"{row['label']}<br>"
        f"{basis_label}: {row['estimate']:,.0f}{estimate_note}; "
        f"{row['estimate_raw_n']:,} sample cases (not a national count)<br>"
        f"Escalated-care share: {row['escalated_share'] * 100:.1f}%{escalated_note}; "
        f"{row['escalated_raw_n']:,} sample cases (not a national count)"
    )


def build_landscape(df: pl.DataFrame, basis: str) -> go.Figure:
    """Excludes any tool suppressed on EITHER axis -- callers use the sibling `suppressed_count`
    function to read how many were excluded (kept separate so this function's own return stays a
    plain `-> go.Figure` matching the plan's interface, one query over `df`, not a tuple).
    Tools limited on EITHER axis are drawn, not excluded: no fill inside a dashed-weight
    construction outline, the product's one confidence treatment. Family colour therefore appears
    only on points whose position is a reportable estimate.
    """
    basis_label = (
        "Estimated annual average" if basis == "annual_average" else "Estimated period total"
    )
    suppressed_mask = (pl.col("estimate_state") == "suppressed") | (
        pl.col("escalated_state") == "suppressed"
    )
    plotted = df.filter(~suppressed_mask)

    fig = go.Figure()
    if plotted.height == 0:
        fig.update_layout(xaxis_type="log")
        _add_captions(fig)
        return _style(fig)

    stable_mask = (plotted["estimate_state"] == "stable") & (plotted["escalated_state"] == "stable")
    stable_df = plotted.filter(stable_mask)
    unstable_df = plotted.filter(~stable_mask)

    # Bubble AREA used to be the estimate -- the same number the log x axis already carries, so
    # one of the chart's channels said nothing new AND misled: a 30k bubble drew 15x the area of
    # a 2k one while sitting 1.2 decades to its right. Area now carries the SAMPLE COUNT behind
    # the point, which is information the chart had nowhere else and which is exactly what a
    # reader needs to weigh a point sitting high on the severity axis.
    max_raw_n = plotted["estimate_raw_n"].max()
    size_ref = (2.0 * max_raw_n / (34.0**2)) if max_raw_n else 1.0

    if stable_df.height:
        rows = list(stable_df.iter_rows(named=True))
        _add_ci_ticks(fig, rows)
        fig.add_trace(
            go.Scatter(
                x=[r["estimate"] for r in rows],
                y=[r["escalated_share"] for r in rows],
                mode="markers",
                name="Stable",
                showlegend=False,
                marker={
                    "size": [r["estimate_raw_n"] for r in rows],
                    "sizemode": "area",
                    "sizeref": size_ref,
                    "sizemin": 5,
                    "color": [FAMILY_COLORS.get(r["family_id"], GRAPHITE_SOFT) for r in rows],
                    # Solid hairline outline = a finished, stable estimate (the confidence
                    # channel; `theme.stability_line("stable")`).
                    "line": {"width": 1, "color": GRAPHITE},
                    "opacity": 0.85,
                },
                text=[r["label"] for r in rows],
                hovertext=[_hover_text(r, basis_label) for r in rows],
                hoverinfo="text",
            )
        )
    if unstable_df.height:
        rows = list(unstable_df.iter_rows(named=True))
        fig.add_trace(
            go.Scatter(
                x=[r["estimate"] for r in rows],
                y=[r["escalated_share"] for r in rows],
                mode="markers",
                name="Limited sample",
                showlegend=False,
                marker={
                    "size": [r["estimate_raw_n"] for r in rows],
                    "sizemode": "area",
                    "sizeref": size_ref,
                    "sizemin": 5,
                    # No fill plus a construction-weight outline: the same "provisional geometry"
                    # treatment the body map and every other mark uses, never a second colour.
                    "color": "rgba(0,0,0,0)",
                    "line": {"width": 1.2, "color": CONSTRUCTION},
                },
                text=[r["label"] for r in rows],
                hovertext=[_hover_text(r, basis_label) for r in rows],
                hoverinfo="text",
            )
        )

    _add_point_labels(fig, plotted)
    _add_legend_keys(fig, plotted["family_id"].to_list())
    fig.update_xaxes(
        type="log",
        title=basis_label,
        tickvals=log_ticks(plotted["estimate"].min(), plotted["estimate"].max()),
    )
    fig.update_yaxes(title="Escalated-care share", tickformat=".0%")
    fig.add_vline(
        x=plotted["estimate"].median(), line_dash="dot", line_color=GRAPHITE_SOFT, line_width=1
    )
    fig.add_hline(
        y=plotted["escalated_share"].median(),
        line_dash="dot",
        line_color=GRAPHITE_SOFT,
        line_width=1,
    )
    _add_captions(fig)
    return _style(fig)


def labelled_tools(df: pl.DataFrame) -> list[str]:
    """Which plotted tools get a printed name: the `LABEL_TOP_BURDEN` highest-burden points, plus
    the `LABEL_SEVERITY_EXTREMES` highest and lowest on the severity axis.

    Only STABLE points are eligible. Printing a name beside a limited-sample point would make the
    provisional marks the most legible objects on the chart -- the inversion this whole rebuild
    exists to undo -- and a reader who reads a name reads the position it sits at as a fact.
    Ties break on label so the selection is deterministic across renders.
    """
    stable = df.filter(
        (pl.col("estimate_state") == "stable") & (pl.col("escalated_state") == "stable")
    )
    if stable.height == 0:
        return []
    rows = list(stable.iter_rows(named=True))
    by_burden = sorted(rows, key=lambda r: (-r["estimate"], r["label"]))
    by_severity = sorted(rows, key=lambda r: (-r["escalated_share"], r["label"]))
    chosen = [r["label"] for r in by_burden[:LABEL_TOP_BURDEN]]
    for r in by_severity[:LABEL_SEVERITY_EXTREMES] + by_severity[-LABEL_SEVERITY_EXTREMES:]:
        if r["label"] not in chosen:
            chosen.append(r["label"])
    return chosen


# Labels sit in muted ink on a short leader line, above-right of their point: quieter than the
# marks they name, so the shape of the cloud still reads first.
_LABEL_OFFSET_X = 10
_LABEL_OFFSET_Y = -12


def _add_point_labels(fig: go.Figure, plotted: pl.DataFrame) -> None:
    names = set(labelled_tools(plotted))
    if not names:
        return
    for row in plotted.iter_rows(named=True):
        if row["label"] not in names:
            continue
        fig.add_annotation(
            x=math.log10(row["estimate"]) if row["estimate"] > 0 else 0,
            y=row["escalated_share"],
            text=row["label"],
            showarrow=True,
            arrowhead=0,
            arrowwidth=1,
            arrowcolor=RULE,
            ax=_LABEL_OFFSET_X,
            ay=_LABEL_OFFSET_Y,
            xanchor="left",
            yanchor="bottom",
            font={"size": 11, "color": GRAPHITE_SOFT},
            bgcolor=VELLUM,
            borderpad=1,
        )


def _add_ci_ticks(fig: go.Figure, rows: list[dict]) -> None:
    """A thin vertical 95% CI tick on the severity axis for each stable point, so a tool at
    4% +/- 3% cannot sit as an equal to one at 8% +/- 0.5%. Drawn under the markers, in
    construction weight, with hover suppressed so it never competes with the point's own tooltip.
    """
    xs: list[float] = []
    ys: list[float] = []
    for r in rows:
        low, high = r.get("escalated_ci_low"), r.get("escalated_ci_high")
        if low is None or high is None:
            continue
        xs.extend([r["estimate"], r["estimate"], None])
        ys.extend([low, high, None])
    if not xs:
        return
    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            line={"width": 1, "color": CONSTRUCTION},
            showlegend=False,
            hoverinfo="skip",
        )
    )


def log_ticks(low: float, high: float) -> list[float]:
    """1-2-5 tick values covering `[low, high]` (e.g. 1k, 2k, 5k, 10k, 20k): enough labelled
    positions to read a log axis, without Plotly's default of labelling every 2..9 minor step."""
    if not low or not high or low <= 0:
        return []
    ticks: list[float] = []
    decade = 10.0 ** math.floor(math.log10(low))
    while decade <= high * 10:
        for step in (1, 2, 5):
            value = step * decade
            if low / 1.5 <= value <= high * 1.5:
                ticks.append(value)
        decade *= 10
    return ticks


def family_label(family_id: str) -> str:
    """Display label for a `FAMILY_COLORS` key: "cutting_shaping" -> "Cutting / Shaping", which is
    exactly `dim_tool_family.label` (asserted in tests), so this pure builder needs no DB call."""
    return " / ".join(part.capitalize() for part in family_id.split("_"))


def _add_legend_keys(fig: go.Figure, family_ids: list[str]) -> None:
    """Legend-only keys. Colour = tool family (one swatch per family present, in palette order);
    fill vs outline = stable vs limited sample, keyed in neutral grey. Letting the plotted traces
    draw their own swatches showed "Stable" in whichever family happened to come first -- a key
    implying a colour meaning it does not have."""
    present = set(family_ids)
    for family_id, color in FAMILY_COLORS.items():
        if family_id in present:
            fig.add_trace(_legend_key(family_label(family_id), {"size": 9, "color": color}))
    fig.add_trace(
        _legend_key(
            "Stable", {"size": 9, "color": GRAPHITE_SOFT, "line": {"width": 1, "color": GRAPHITE}}
        )
    )
    fig.add_trace(
        _legend_key(
            "Limited sample",
            {"size": 9, "color": "rgba(0,0,0,0)", "line": {"width": 1.2, "color": CONSTRUCTION}},
        )
    )
    fig.add_trace(_legend_key("Bubble size: sample cases", {"size": 9, "color": RULE}))


def _legend_key(name: str, marker: dict) -> go.Scatter:
    return go.Scatter(
        x=[None],
        y=[None],
        mode="markers",
        name=name,
        marker=marker,
        hoverinfo="skip",
    )


# Fixed figure geometry (px): the bottom band stacks tick labels, the x axis title, the
# x-orientation caption (`_CAPTION_X_DROP` below the plot) and the legend (`_LEGEND_DROP`).
_HEIGHT = 480
_MARGIN_TOP = 32
_MARGIN_BOTTOM = 104
_CAPTION_X_DROP = 44
_LEGEND_DROP = 70


def _style(fig: go.Figure) -> go.Figure:
    """Log-x labelled on a 1-2-5 sequence (`log_ticks`): the default log axis labelled every 2..9
    minor step in 8px type, which read as noise. The band above the
    plot holds the y-orientation caption; the band below holds the x axis title, the
    x-orientation caption right-aligned under it, and the legend at the foot -- none can touch a
    tick label.
    """
    style_chart(
        fig,
        height=_HEIGHT,
        margin={"t": _MARGIN_TOP, "b": _MARGIN_BOTTOM, "l": 8, "r": 16},
        grid="xy",
        legend=True,
    )
    # The key hangs from a fixed line below the x-orientation caption, left-aligned, and grows
    # DOWNWARD when it wraps on a phone (Plotly pushes the bottom margin for an outside legend).
    # Anchored above the plot, a wrapped key rode up over the y-orientation caption.
    plot_height = _HEIGHT - _MARGIN_TOP - _MARGIN_BOTTOM
    fig.update_layout(
        legend={
            "x": 0.0,
            "xanchor": "left",
            "y": -_LEGEND_DROP / plot_height,
            "yanchor": "top",
        }
    )
    fig.update_xaxes(
        tickformat="~s",
        title_standoff=10,
    )
    fig.update_yaxes(title_standoff=10, rangemode="tozero")
    return fig


def _add_captions(fig: go.Figure) -> None:
    """The two neutral orientation captions (D-09) -- "higher", never "safer"/"more dangerous".
    Placed in the reserved bands above and below the plot so they label the direction of each axis
    without colliding with its tick labels or title."""
    fig.add_annotation(
        **chart_note(
            CAPTION_X, xref="paper", yref="paper", x=1.0, y=0.0, xanchor="right", yanchor="top"
        ),
        yshift=-_CAPTION_X_DROP,
    )
    fig.add_annotation(
        **chart_note(
            CAPTION_Y, xref="paper", yref="paper", x=0.0, y=1.0, xanchor="left", yanchor="bottom"
        ),
        yshift=6,
    )


def suppressed_count(df: pl.DataFrame) -> int:
    """Count of tools excluded from `build_landscape` (suppressed on either axis) -- the caller
    (overview.py) uses this to compose the caption noting how many tools were omitted."""
    suppressed_mask = (pl.col("estimate_state") == "suppressed") | (
        pl.col("escalated_state") == "suppressed"
    )
    return int(df.filter(suppressed_mask).height)
