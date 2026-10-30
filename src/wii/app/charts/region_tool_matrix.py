"""BODY PART x TOOL MATRIX: rows = tools (top 15 by estimated burden by default, "Show all tools"
toggle), columns = the body regions in `dim_body_region.sort_order` -- value = within-tool share of
injuries involving that region (any recorded, so rows can sum past 100%, per the page caption).

ONE figure serves 1,440px and 390px, so the width problem is solved by removing width demand
rather than by hoping for it:

  * **Empty columns are dropped.** Only 11 of the 22 regions carry a single reportable cell for
    the default selection; the other 11 were drawing an axis label each and nothing else. The
    dropped count is stated on the figure, so a reader knows the body was not edited for looks.
  * **Column labels are rotated** 45 degrees. Twenty-two horizontal labels ran together into
    "HeadFaceEyeMouth Ear Ne...Shoulder,Arm, upElbowrm, lo"; rotation costs bottom margin, which
    `automargin` already pays for.
  * **Tool labels are truncated** to `MAX_TOOL_LABEL_CHARS`. "Portable Circular Power Saw" was
    eating 60% of a 390px viewport, which left ~5px per column. Full names stay on hover.
  * **The colorbar is horizontal, above the plot**, where nothing else lives. Vertical, it stole
    the width the matrix needs and at 390px its ticks landed on top of the cells.
  * **In-cell numbers are off by default** (`show_text=True` to restore). A percentage in a 20px
    cell is a speck; hover carries every number, exactly as the review asked.

Suppression is the majority state here (283 of 330 cells at the default filter), so it is carried
by ABSENCE plus a counted note, NOT by a dashed outline per cell -- 283 dashed rectangles is graph
paper, which is the amber-hatch failure in a different ink. The dashed construction outline is
reserved for LIMITED cells, the ones that carry a number a reader could over-trust.

Pure figure-builder -- no Streamlit/wii.metrics calls happen in this module; every input is an
already-resolved Polars frame built by `wii.app.data.region_share_by_tool_frame`.
"""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl

from wii.app.charts import marks
from wii.app.components.stability_badge import (
    LIMITED_SAMPLE_LABEL,
    STATE_STABLE,
    SUPPRESSED_LABEL,
)
from wii.app.theme import (
    CHART_DATA_LABEL_SIZE,
    GRAPHITE,
    GRAPHITE_SOFT,
    SEQUENTIAL_SCALE,
    TYPE_LABEL,
    chart_note,
    style_chart,
)

SUBTITLE = (
    "Anatomical patterns vary by tool; this matrix normalizes within each tool so a saw's "
    "finger-heavy signature isn't hidden by its overall volume."
)
#: Hand and finger dominate every saw row; uncapped, they saturate the scale and leave every
#: other region reading as "nothing here". The colour domain is capped and the top tick says so,
#: which keeps the rest of the body legible without misstating the capped cells (their exact
#: share is printed in the cell and repeated on hover).
DEFAULT_ZCAP = 0.5
#: Below roughly this cell width a printed percentage renders as an illegible speck, so the
#: number goes to hover only -- which is why `show_text` defaults to False. 11 regions across a
#: 1,100px plot is ~90px; across a 390px phone it is ~22px.
MIN_TEXT_CELL_PX = 36
#: Region column labels are abbreviated to this many characters before being rotated.
MAX_LABEL_CHARS = 10
#: Tool row labels. At 390px an untruncated "Portable Circular Power Saw" took 60% of the
#: viewport; the full name still reaches hover.
MAX_TOOL_LABEL_CHARS = 16

ROW_HEIGHT = 28
#: Axis labels, the rotated column labels and the colorbar band above the plot.
_CHROME_HEIGHT = 196
_MIN_HEIGHT = 240
#: Rotated 45-degree column labels stand about this tall; the coverage note sits below them and
#: the bottom margin has to pay for both, since an annotation never grows a margin by itself.
_LABEL_BAND_PX = 76
#: Rotated labels, then the two-line coverage note under them.
_BOTTOM_MARGIN = _LABEL_BAND_PX + 54
#: Reserved band above the plot for the horizontal colorbar.
_TOP_MARGIN = 54

_STATE_LABEL = {
    STATE_STABLE: "Stable",
    "limited": LIMITED_SAMPLE_LABEL,
    "suppressed": SUPPRESSED_LABEL,
}

_HOVERTEMPLATE = (
    "%{customdata[6]}, %{customdata[4]}<br>Share of this tool's injuries: %{customdata[0]}"
    "<br>95% CI: %{customdata[1]}<br>Estimated injuries: %{customdata[2]}"
    "<br>Raw N: %{customdata[3]} sample cases (not a national count)"
    "<br>%{customdata[5]}<extra></extra>"
)


def _abbreviate(label: str, max_chars: int = MAX_LABEL_CHARS) -> str:
    """Abbreviate at the first " (" (e.g. "Face (including eyelid, eye area and nose)" -> "Face"),
    then hard-truncate to `max_chars`. The full label always still reaches hover (region labels
    via customdata[4], tool labels via customdata[6])."""
    idx = label.find(" (")
    short = label[:idx] if idx != -1 else label
    if len(short) <= max_chars:
        return short
    return short[: max_chars - 1].rstrip() + "…"


def _coverage_lines(suppressed: int, total_cells: int, hidden_regions: int) -> list[str]:
    """The honesty line under the grid, as separate lines: how many cells are blank because they
    are below the reporting floor, and how many regions were left off for having nothing to show.
    Without it a mostly-empty matrix reads as "these tools do not injure those parts" rather than
    "too few sample cases to report" -- and dropping empty columns would be an undisclosed edit.

    Two lines rather than one sentence because at 390px a single 100-character line runs off the
    sheet, and a Plotly annotation neither wraps nor shrinks.
    """
    lines: list[str] = []
    if suppressed:
        lines.append(f"{suppressed} of {total_cells} cells are below the reporting floor (blank)")
    if hidden_regions:
        lines.append(f"{hidden_regions} regions with no reportable cell are not shown")
    return lines


def coverage_note(
    df: pl.DataFrame,
    region_order: list[str],
    tool_order: list[str],
    *,
    drop_empty_regions: bool = True,
) -> str:
    """The same disclosure as one sentence, for a page caption. The figure carries it too; this
    is here for a caller that would rather say it in prose beside the chart."""
    kept = drawn_regions(df, region_order, tool_order) if drop_empty_regions else list(region_order)
    reportable = {
        (row["tool_id"], row["region_id"])
        for row in df.iter_rows(named=True)
        if row["state"] != "suppressed" and row["value"] is not None
    }
    suppressed = sum(
        1 for tool_id in tool_order for region_id in kept if (tool_id, region_id) not in reportable
    )
    lines = _coverage_lines(suppressed, len(tool_order) * len(kept), len(region_order) - len(kept))
    return "; ".join(lines) + "." if lines else ""


def drawn_regions(df: pl.DataFrame, region_order: list[str], tool_order: list[str]) -> list[str]:
    """The regions with at least one reportable cell among `tool_order`. A column that is empty
    for every tool in view is not earning its width -- and at 22 columns that width is the whole
    legibility problem. Falls back to the full order when nothing is reportable, so the chart
    never renders with no x axis at all."""
    tools = set(tool_order)
    live = {
        row["region_id"]
        for row in df.iter_rows(named=True)
        if row["tool_id"] in tools and row["state"] != "suppressed" and row["value"] is not None
    }
    kept = [rid for rid in region_order if rid in live]
    return kept or list(region_order)


def build_region_tool_matrix(
    df: pl.DataFrame,
    region_order: list[str],
    region_labels: dict[str, str],
    tool_order: list[str],
    tool_labels: dict[str, str],
    *,
    show_text: bool = False,
    zcap: float | None = DEFAULT_ZCAP,
    drop_empty_regions: bool = True,
) -> go.Figure:
    """`df` is `wii.app.data.region_share_by_tool_frame`'s output (tool_id, tool_label, region_id,
    value, ci_low, ci_high, num_estimate, raw_n, state) -- ALREADY basis/stability-resolved by the
    caller. `region_order`/`tool_order` fix the x/y axis order (the region `sort_order`, the
    page's top-N-or-all selection for tools).

    `show_text=True` prints the percentage in each cell; it is OFF by default because a cell is
    ~22px wide at 390px (`MIN_TEXT_CELL_PX`) and hover carries every number anyway. `zcap` caps
    the COLOUR domain only: a cell above the cap is drawn at the top stop and its colorbar tick
    reads ">50%", while its hover text is always the real share. `drop_empty_regions=False`
    keeps every column, including the ones with nothing reportable in them.
    """
    by_cell = {(row["tool_id"], row["region_id"]): row for row in df.to_dicts()}
    all_regions = list(region_order)
    region_order = drawn_regions(df, all_regions, tool_order) if drop_empty_regions else all_regions
    hidden_regions = len(all_regions) - len(region_order)
    suppressed_cells = 0

    z: list[list[float | None]] = []
    text: list[list[str]] = []
    customdata: list[list[list]] = []
    unstable_cells: list[tuple[int, int, str]] = []
    for y_index, tool_id in enumerate(tool_order):
        z_row: list[float | None] = []
        text_row: list[str] = []
        cd_row: list[list] = []
        for x_index, region_id in enumerate(region_order):
            cell = by_cell.get((tool_id, region_id))
            state = cell["state"] if cell is not None else "suppressed"
            value = cell["value"] if cell is not None else None
            full_label = region_labels.get(region_id, region_id)
            raw_n = cell["raw_n"] if cell is not None else 0

            if cell is None or state == "suppressed" or value is None:
                z_row.append(None)
                text_row.append(marks.NOT_REPORTABLE_SHORT)
                estimate_text = marks.NOT_REPORTABLE
                share_text = marks.NOT_REPORTABLE
                suppressed_cells += 1
            else:
                # The COLOUR is capped; the number never is.
                z_row.append(min(value, zcap) if zcap is not None else value)
                display = marks.fmt_share(value)
                text_row.append(display)
                num_estimate = cell.get("num_estimate")
                estimate_text = marks.fmt_estimate(num_estimate)
                share_text = display
                if state != STATE_STABLE:
                    unstable_cells.append((x_index, y_index, "limited"))

            ci_low, ci_high = (
                (cell.get("ci_low"), cell.get("ci_high")) if cell is not None else (None, None)
            )
            ci_text = (
                f"{marks.fmt_share(ci_low)}–{marks.fmt_share(ci_high)}"
                if ci_low is not None and ci_high is not None
                else marks.NOT_REPORTABLE
            )
            state_label = _STATE_LABEL.get(state, SUPPRESSED_LABEL)
            cd_row.append(
                [
                    share_text,
                    ci_text,
                    estimate_text,
                    raw_n,
                    full_label,
                    state_label,
                    tool_labels.get(tool_id, tool_id),
                ]
            )
        z.append(z_row)
        text.append(text_row)
        customdata.append(cd_row)

    non_suppressed = [v for row in z for v in row if v is not None]
    zmax = zcap if zcap is not None else (max(non_suppressed) if non_suppressed else 1.0)
    capped = any((cell.get("value") or 0.0) > zmax for cell in by_cell.values() if cell is not None)
    # Three ticks, always including both ends of the real colour domain. A finer ladder does not
    # fit a 390px colorbar and Plotly's answer to that is to rotate the labels 90 degrees, which
    # is worse than having fewer of them. The top tick reads ">50%" only when something is
    # actually capped there.
    tickvals = [0.0, zmax / 2, zmax]
    ticktext = [marks.fmt_share(v) for v in tickvals]
    if capped:
        ticktext[-1] = f">{ticktext[-1]}"

    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=[_abbreviate(region_labels.get(rid, rid)) for rid in region_order],
            y=[_abbreviate(tool_labels.get(tid, tid), MAX_TOOL_LABEL_CHARS) for tid in tool_order],
            colorscale=SEQUENTIAL_SCALE,
            zmin=0,
            zmax=zmax,
            xgap=1,
            ygap=1,
            text=text,
            texttemplate="%{text}" if show_text else None,
            textfont={"size": CHART_DATA_LABEL_SIZE},
            customdata=customdata,
            hovertemplate=_HOVERTEMPLATE,
            # Horizontal, in the band above the plot: vertical it took width the matrix needs,
            # and at 390px its tick labels sat on top of the cells.
            colorbar={
                "title": {"text": "Share of this tool's cases", "side": "top"},
                "orientation": "h",
                "tickformat": ".0%",
                "tickvals": tickvals,
                "ticktext": ticktext,
                "thickness": 8,
                "outlinewidth": 0,
                "len": 0.9,
                "x": 0,
                "xanchor": "left",
                "xref": "paper",
                "y": 1.0,
                "yanchor": "bottom",
                "yref": "paper",
                "ticklabelposition": "outside bottom",
                "tickangle": 0,
            },
        )
    )
    # Only LIMITED cells get the dashed construction outline. A suppressed cell is bare, and the
    # note below counts them: 283 dashed rectangles would be graph paper, not a signal.
    marks.add_cell_outlines(fig, unstable_cells)
    style_chart(
        fig,
        height=max(_MIN_HEIGHT, ROW_HEIGHT * len(tool_order) + _CHROME_HEIGHT),
        margin={"l": 8, "r": 8, "t": _TOP_MARGIN, "b": _BOTTOM_MARGIN},
        grid="none",
    )
    lines = _coverage_lines(suppressed_cells, len(tool_order) * len(region_order), hidden_regions)
    if lines:
        fig.add_annotation(
            **chart_note(
                "<br>".join(lines),
                xref="paper",
                # Anchored to the plot's RIGHT edge so the text runs leftwards across the figure,
                # over the (empty) band under the row labels. Anchored left it would start at the
                # plot's left edge, which at 390px leaves it ~140px and runs it off the sheet.
                x=1,
                xanchor="right",
                align="left",
                yref="paper",
                y=0,
                yanchor="top",
                # Clear of the rotated column labels, which own the band directly under the axis.
                yshift=-_LABEL_BAND_PX,
            )
        )
    fig.update_xaxes(tickangle=-45, tickfont={"size": TYPE_LABEL, "color": GRAPHITE_SOFT})
    fig.update_yaxes(tickfont={"size": TYPE_LABEL, "color": GRAPHITE})
    return fig
