"""TOP TOOLS BY ESTIMATED ER BURDEN (PAGE-01, D-09) and reused by Tool Profiles (03-03) for any
other CI-whisker ranked view. Reuses `stability_badge.order_ranked`/`resolve` directly (per
03-02-PLAN's key_links) rather than re-deriving ordering/stability here -- this module's only job
is turning already-ordered, already-resolved rows into a `go.Figure`.
"""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl

from wii.app.charts import marks
from wii.app.components import stability_badge
from wii.app.theme import (
    CHART_DATA_LABEL_SIZE,
    CONSTRUCTION,
    FONT_NARROW,
    GRAPHITE,
    GRAPHITE_SOFT,
    PRIMARY,
    RULE,
    TYPE_LABEL,
    chart_note,
    style_chart,
)

SUBTITLE = (
    "Stable estimates are ranked first; limited-sample tools are shown separately, never "
    "blended into the ranking. Whiskers are 95% confidence intervals."
)

#: Vertical pixels per ranked row, and the fixed chrome (axis + margins) around the rows. Height
#: grows with the row count so 16 tools never get crushed into Plotly's default 450px.
ROW_HEIGHT = 22
_CHROME_HEIGHT = 64
_MIN_HEIGHT = 200

#: Headroom past the longest whisker so the printed value label has air inside the explicit range.
_X_HEADROOM = 1.18

ALL_SUPPRESSED_TEMPLATE = (
    "No row in this view clears the reporting floor; all {n} are below it: {labels}. "
    "{raw_n:,} sample cases in total (not a national count)."
)
SUPPRESSED_FOOTER_TEMPLATE = "{n} below the reporting floor: {labels}."


def _hover_text(row: dict, resolved, basis: str, value_label: str) -> str:
    basis_label = "annual average" if basis == "annual_average" else "period total"
    state_label = {
        stability_badge.STATE_STABLE: "stable",
        stability_badge.STATE_LIMITED: stability_badge.LIMITED_SAMPLE_LABEL,
        stability_badge.STATE_SUPPRESSED: stability_badge.SUPPRESSED_LABEL,
    }[resolved.state]
    raw_n_text = f"{resolved.raw_n:,} sample cases (not a national count)"
    return (
        f"{row['display_label']}<br>{value_label} ({basis_label}): {resolved.display_value}"
        f"<br>{raw_n_text}<br>{state_label}"
    )


def _first_limited_index(resolved: list[tuple[dict, object]]) -> int | None:
    for i, (_, res) in enumerate(resolved):
        if res.state == stability_badge.STATE_LIMITED:
            return i
    return None


def _resolved_rows(df: pl.DataFrame, basis: str) -> list[tuple[dict, object]]:
    rows = list(df.iter_rows(named=True))
    ordered_rows = stability_badge.order_ranked(rows, basis)
    return [(row, stability_badge.resolve(row, basis)) for row in ordered_rows]


def suppressed_labels(df: pl.DataFrame, basis: str) -> tuple[str, ...]:
    """Display labels of every row this frame suppresses, in the same ranked order the chart
    would have placed them. The companion to `suppressed_footer=True`: the builder drops those
    rows off the axis, the PAGE names them in a caption, so a suppressed category is still
    counted and named -- never silently gone (D-07)."""
    return tuple(
        row["display_label"]
        for row, res in _resolved_rows(df, basis)
        if res.state == stability_badge.STATE_SUPPRESSED
    )


def suppressed_footer_note(df: pl.DataFrame, basis: str) -> str | None:
    """One caption line naming the rows `suppressed_footer=True` left off the axis, or `None`
    when nothing was suppressed."""
    labels = suppressed_labels(df, basis)
    if not labels:
        return None
    return SUPPRESSED_FOOTER_TEMPLATE.format(n=len(labels), labels=", ".join(labels))


def all_suppressed_note(df: pl.DataFrame, basis: str, noun: str = "rows") -> str:
    """The single honest line a page prints INSTEAD of an all-suppressed chart (MECH-10). Plotly
    autoranges a zero-width axis when every bar is zero-length and `~s` then emits SI milli-units
    ("−500m … 500m") beside a column of "Not reportable" rows; there is no version of that
    chart worth drawing, so the builder returns `None` and the page prints this."""
    resolved = _resolved_rows(df, basis)
    labels = [row["display_label"] for row, _ in resolved]
    raw_n = sum(res.raw_n or 0 for _, res in resolved)
    return ALL_SUPPRESSED_TEMPLATE.format(
        n=f"{len(labels)} {noun}", labels=", ".join(labels), raw_n=raw_n
    )


def build_ranked_bars(
    df: pl.DataFrame,
    basis: str,
    value_label: str,
    *,
    suppressed_footer: bool = False,
) -> go.Figure | None:
    """`df` is the raw `estimate_by(..., by="tool")`-shaped frame (EstimateRecord fields flattened
    plus `display_label`) -- unresolved, since `order_ranked`/`resolve` do the basis/stability
    work.

    Returns `None` when NO row is drawable (every row suppressed, or an empty frame): an
    all-suppressed ranked chart has a zero-width value axis, so Plotly autoranges it to
    [-1, 1] and the `~s` tick format prints milli-units beside a column of "Not reportable" rows.
    The page prints `all_suppressed_note(df, basis)` instead (MECH-10).

    `suppressed_footer=True` omits suppressed rows from the axis entirely (OV-08: 14 blank ranks
    is half a chart of empty space); the page names them with `suppressed_footer_note(df, basis)`
    so nothing is silently dropped. The default keeps every suppressed row on the axis with a
    "Not reportable" text label and a zero-length (hidden) bar.
    """
    resolved = _resolved_rows(df, basis)
    drawable = [
        (row, res) for row, res in resolved if res.state != stability_badge.STATE_SUPPRESSED
    ]
    if not drawable:
        return None
    if suppressed_footer:
        resolved = drawable

    labels = [row["display_label"] for row, _ in resolved]
    values = [res.value if res.value is not None else 0.0 for _, res in resolved]
    # A suppressed row reads "Not reportable" at the baseline (D-07), never a zero.
    # Numeric values are printed by the separate label trace below, past the end of each CI whisker.
    text = [
        "Not reportable" if res.state == stability_badge.STATE_SUPPRESSED else ""
        for _, res in resolved
    ]
    # LINE TREATMENT = CONFIDENCE. A limited bar keeps its length (the estimate is still the
    # estimate) but is filled one step lighter and outlined with a dashed construction line drawn
    # by `marks.add_confidence_outlines` below. No hatch, no amber, no colour-coded stability --
    # the fill channel is left free to mean magnitude.
    states = [res.state for _, res in resolved]
    fills = [marks.fill_for(PRIMARY, state) for state in states]
    # Only stable bars carry the solid trace outline; the dashed shapes stand in for the rest.
    outline_widths = [1 if state == stability_badge.STATE_STABLE else 0 for state in states]

    error_plus: list[float] = []
    error_minus: list[float] = []
    for _, res in resolved:
        if (
            res.state == stability_badge.STATE_SUPPRESSED
            or res.ci_high is None
            or res.ci_low is None
        ):
            error_plus.append(0.0)
            error_minus.append(0.0)
        else:
            error_plus.append(max(0.0, res.ci_high - res.value))
            error_minus.append(max(0.0, res.value - res.ci_low))

    hover = [_hover_text(row, res, basis, value_label) for row, res in resolved]

    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            text=text,
            textposition="outside",
            cliponaxis=False,
            textfont={
                "family": FONT_NARROW,
                "size": CHART_DATA_LABEL_SIZE,
                "color": GRAPHITE_SOFT,
            },
            marker={
                "color": fills,
                "line": {"color": GRAPHITE, "width": outline_widths},
            },
            error_x={
                "type": "data",
                "array": error_plus,
                "arrayminus": error_minus,
                "visible": True,
                "color": GRAPHITE_SOFT,
                "thickness": 1,
                "width": 3,
            },
            hovertext=hover,
            hoverinfo="text",
        )
    )
    # Every plotted bar carries its value, placed just past the whisker's end so the number never
    # sits on the interval line and a short bar never has to be traced back to the axis.
    shown = [
        (i, res)
        for i, (_, res) in enumerate(resolved)
        if res.state != stability_badge.STATE_SUPPRESSED
    ]
    fig.add_trace(
        go.Scatter(
            x=[values[i] + error_plus[i] for i, _ in shown],
            y=[labels[i] for i, _ in shown],
            mode="text",
            # En-space lead-in: clear air between the whisker cap and the number.
            text=[f"\u2002{res.display_value}" for _, res in shown],
            textposition="middle right",
            textfont={"family": FONT_NARROW, "size": CHART_DATA_LABEL_SIZE, "color": GRAPHITE},
            cliponaxis=False,
            hoverinfo="skip",
            showlegend=False,
        )
    )
    fig.update_layout(bargap=0.3)
    marks.add_confidence_outlines(fig, values=values, states=states)
    style_chart(
        fig,
        height=max(_MIN_HEIGHT, ROW_HEIGHT * len(labels) + _CHROME_HEIGHT),
        margin={"l": 8, "r": 64, "t": 8, "b": 48},
        grid="x",
    )
    fig.update_yaxes(
        autorange="reversed",
        tickfont={"size": TYPE_LABEL, "color": GRAPHITE},
        ticklabelstandoff=8,
        showline=True,
        linecolor=RULE,
    )
    # An EXPLICIT value range, always. Plotly autoranges a zero-width axis to [-1, 1] and the
    # `~s` tick format then prints SI milli-units beside a column of dashes; with the
    # range pinned to [0, longest whisker + headroom] that bug class cannot recur, and the
    # printed value labels have room to sit past the whisker caps.
    x_max = max((v + e for v, e in zip(values, error_plus, strict=True)), default=0.0)
    fig.update_xaxes(
        title_text=value_label,
        tickformat="~s",
        title_standoff=8,
        range=[0, (x_max or 1.0) * _X_HEADROOM],
    )

    divider_index = _first_limited_index(resolved)
    if divider_index is not None:
        # A labelled rule between the ranked stable block and the limited-sample block: the label
        # sits ON the rule at the right edge (where the short limited bars leave room), so it reads
        # as the heading of the group below it rather than a note floating beside one bar.
        fig.add_hline(y=divider_index - 0.5, line_dash="dot", line_color=CONSTRUCTION, line_width=1)
        fig.add_annotation(
            **chart_note(
                "Limited sample",
                xref="paper",
                x=1.0,
                xanchor="right",
                yref="y",
                y=divider_index - 0.5,
                yanchor="bottom",
            )
        )
    return fig
