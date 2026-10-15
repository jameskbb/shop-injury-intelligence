"""The confidence channel, drawn as Plotly marks.

`docs/design-system.md` splits the two things a data mark has to say into two orthogonal
channels: **ink density carries magnitude** and **line treatment carries confidence**. Amber is
retired, so nothing in this module ever colour-codes stability.

Plotly's `go.Bar.marker.line` takes a colour and a width but NOT a dash, so a dashed bar outline
has to be drawn as a layout shape. `add_confidence_outlines` does exactly that: one hairline
rectangle per non-stable bar, dashed per `theme.stability_line`, over a fill that has already been
lightened a step. Every chart in `wii.app.charts` that draws bars goes through here, so "limited"
looks identical on the Overview ranked bars, a tool's diagnosis profile and the Mechanisms page.
"""

from __future__ import annotations

import plotly.graph_objects as go

from wii.app import theme
from wii.app.components.stability_badge import (
    STATE_LIMITED,
    STATE_STABLE,
    STATE_SUPPRESSED,
    format_estimate,
    format_share,
)

# ---------------------------------------------------------------------------
# Numbers. ONE formatting layer for every chart, delegating to the same
# `stability_badge` formatters the KPI cards and tables use -- so 78.4% is written the same way on
# a bar label, in a heatmap cell, in a tooltip and on a card, and no builder grows its own
# f-string. `EM_DASH` is the single "there is no number here" mark.
# ---------------------------------------------------------------------------

EM_DASH = "—"


def fmt_share(value: float | None) -> str:
    """A share as a 1-decimal percentage ("78.4%"), or the em dash when there is none."""
    return format_share(value) if value is not None else EM_DASH


def fmt_estimate(value: float | None) -> str:
    """An estimated-injury count, rounded to the nearest 100 with thousands separators
    ("2,418,400"), or the em dash when there is none."""
    return format_estimate(value) if value is not None else EM_DASH


def fmt_points(delta: float | None) -> str:
    """A difference between two shares, in percentage POINTS and always signed ("+3.2 pts") --
    never a percentage, which is what a reader would otherwise take a share delta to be."""
    if delta is None:
        return EM_DASH
    return f"{delta * 100:+.1f} pts"


#: Transparent: a suppressed mark carries NO fill at all (bare vellum), which is what makes it
#: unmistakable without giving it a colour of its own.
NO_FILL = "rgba(0,0,0,0)"

#: Fallback lightening step, for a mark with no category colour of its own.
#:
#: Prefer `fill_for(colour, state)`, which lightens the mark's OWN ink. A single shared stop here
#: would strip a limited bar of its category hue, so a limited laceration bar and a limited
#: fracture bar would become the same colour -- confidence would be silently eating the category
#: channel, which is exactly what the design contract separates. `components/body_map.py` blends
#: the region's own colour the same way; the two surfaces must agree.
LIMITED_FILL = theme.SEQUENTIAL_SCALE[1]

#: How far a limited mark's fill travels toward the sheet. Matches `components/body_map._blend`.
_LIMITED_BLEND = 0.45


def _blend(color: str, toward: str, amount: float) -> str:
    """`color` mixed `amount` of the way toward `toward`, both `#rrggbb`."""
    a = color.lstrip("#")
    b = toward.lstrip("#")
    if len(a) != 6 or len(b) != 6:
        return color
    mixed = (
        round(int(a[i : i + 2], 16) + (int(b[i : i + 2], 16) - int(a[i : i + 2], 16)) * amount)
        for i in (0, 2, 4)
    )
    return "#" + "".join(f"{c:02x}" for c in mixed)


#: Matches `bargap=0.3`: a bar fills 0.7 of its category slot, so an outline drawn at 0.7 sits
#: exactly on the bar's edge rather than floating off it.
_BAR_WIDTH = 0.7


def fill_for(color: str, state: str) -> str:
    """The fill for a mark of `state`: its own colour when stable, its own colour lightened toward
    the sheet when limited, no fill at all when suppressed.

    A limited mark keeps its CATEGORY hue -- only its density drops. Magnitude and confidence are
    separate channels, so confidence is never allowed to overwrite the category colour.
    """
    if state == STATE_SUPPRESSED:
        return NO_FILL
    if state == STATE_LIMITED:
        if color.startswith("#"):
            return _blend(color, theme.VELLUM, _LIMITED_BLEND)
        return LIMITED_FILL
    return color


def add_confidence_outlines(
    fig: go.Figure,
    *,
    values: list[float | None],
    states: list[str],
    orientation: str = "h",
    width: float = _BAR_WIDTH,
) -> go.Figure:
    """Draws the dashed construction outline around every non-stable bar, in place.

    `values` and `states` are parallel, in the trace's own category order; a bar's category
    position is its index, which is how Plotly addresses a categorical axis in shape coordinates.
    A stable bar gets no shape at all -- its own solid 1px outline is set on the trace.
    """
    for i, (value, state) in enumerate(zip(values, states, strict=True)):
        if state == STATE_STABLE or value is None or value <= 0:
            continue
        line = theme.stability_line(state)
        lo, hi = i - width / 2, i + width / 2
        if orientation == "h":
            box = {"x0": 0, "x1": value, "y0": lo, "y1": hi}
        else:
            box = {"x0": lo, "x1": hi, "y0": 0, "y1": value}
        fig.add_shape(
            type="rect",
            xref="x",
            yref="y",
            fillcolor=NO_FILL,
            line={"color": line["color"], "width": line["width"], "dash": line["dash"]},
            layer="above",
            **box,
        )
    return fig


def add_baseline_ticks(
    fig: go.Figure,
    *,
    baselines: list[float | None],
    orientation: str = "h",
    width: float = _BAR_WIDTH,
) -> go.Figure:
    """A workshop-baseline tick per bar: a short hairline across the bar at the all-workshop
    value, so "74.6% laceration" can be read against the workshop's own 57.6% without a second
    chart. Drawn in graphite at rule strength -- it is annotation, not data."""
    for i, baseline in enumerate(baselines):
        if baseline is None or baseline <= 0:
            continue
        lo, hi = i - width / 2, i + width / 2
        if orientation == "h":
            box = {"x0": baseline, "x1": baseline, "y0": lo, "y1": hi}
        else:
            box = {"x0": lo, "x1": hi, "y0": baseline, "y1": baseline}
        fig.add_shape(
            type="line",
            xref="x",
            yref="y",
            line={"color": theme.GRAPHITE, "width": 1},
            layer="above",
            **box,
        )
    return fig


#: A heatmap with more dashed cells than this is telling the reader "almost nothing here is
#: stable" through sheer line noise; past the cap the caption carries the count instead.
MAX_CELL_OUTLINES = 240


def add_cell_outlines(
    fig: go.Figure,
    cells: list[tuple[int, int, str]],
    *,
    xref: str = "x",
    yref: str = "y",
    max_shapes: int = MAX_CELL_OUTLINES,
) -> int:
    """Dashed construction outlines around non-stable heatmap cells -- the grid equivalent of a
    limited bar's outline, since Plotly gives a `go.Heatmap` no per-cell line or opacity control.

    `cells` is `(x index, y index, state)`. Returns the number of outlines actually drawn, so a
    caller can caption the remainder rather than silently dropping it.
    """
    drawn = 0
    for x, y, state in cells:
        if state == STATE_STABLE or drawn >= max_shapes:
            continue
        line = theme.stability_line(state)
        fig.add_shape(
            type="rect",
            xref=xref,
            yref=yref,
            x0=x - 0.5,
            x1=x + 0.5,
            y0=y - 0.5,
            y1=y + 0.5,
            fillcolor=NO_FILL,
            line={"color": line["color"], "width": line["width"], "dash": line["dash"]},
            layer="above",
        )
        drawn += 1
    return drawn
