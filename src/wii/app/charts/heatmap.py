"""Tool Comparison's INJURY FINGERPRINT and the Mechanisms page's MECHANISM-BY-TOOL matrix.

Plotly cannot mix colorscales per column inside one heatmap trace, so the fingerprint is two
subplots sharing a y axis: a small horizontal BURDEN bar (share of workshop total, on the
sequential ink ramp) beside the 8-column DIVERGING matrix (log2 ratio to the all-workshop
baseline, capped +-2, zmid 0). Separating the burden column out is deliberate -- a share and a
ratio are different quantities and were previously drawn as adjacent cells of what looked like one
matrix.

Two readability rules the review forced, both honesty-shaped rather than decorative:

  * The diverging colorbar is ticked in RATIOS a woodworker reads ("1/4x .. 4x"), never in log2
    units, and the in-cell number is the tool's own share. `FINGERPRINT_CAPTION` states that
    split in one line -- two quantities per cell is fine, unlabelled two quantities is not.
  * Stability is LINE TREATMENT: a limited cell keeps its fill and its number under a dashed
    construction outline (`charts.marks`), instead of carrying a "(limited)" suffix in ~8px type
    in most cells of the grid. A suppressed cell has NO fill and reads "N/R" (not reportable).

`build_share_matrix` is the generic within-group share matrix behind the mechanism heatmap and the
diagnosis signature. Pure figure-builders -- no Streamlit/`wii.metrics` calls happen here.
"""

from __future__ import annotations

import math
import re

import plotly.graph_objects as go
import polars as pl
from plotly.subplots import make_subplots

from wii.analytics.insights import DimensionCategory
from wii.app.charts import marks
from wii.app.components.stability_badge import LIMITED_SAMPLE_LABEL, SUPPRESSED_LABEL
from wii.app.theme import (
    CHART_DATA_LABEL_SIZE,
    DIAGNOSIS_COLORS,
    DIVERGING_SCALE,
    FONT_SANS,
    GRAPHITE,
    GRAPHITE_SOFT,
    MECHANISM_COLORS,
    SEQUENTIAL_SCALE,
    TYPE_LABEL,
    chart_note,
    mechanism_label,
    style_chart,
)

FINGERPRINT_SUBTITLE = (
    "Cell color compares each tool with the all-workshop baseline; hover for the raw values."
)
#: The one line that makes a two-quantity cell readable.
FINGERPRINT_CAPTION = (
    "Colour is this tool's ratio to the all-workshop share; the number in the cell is the tool's "
    "own share. The bar on the left is its share of total workshop burden."
)
DIAGNOSIS_SIGNATURE_SUBTITLE = (
    "Within-tool percentages reveal injury signatures that total counts can hide."
)
MECHANISM_HEATMAP_CAPTION = (
    "Within-tool share of primary mechanism. Unknown is listed last and left uncoloured so it "
    "cannot set the scale; a dashed cell is a limited sample and an empty cell is below the "
    "reporting floor."
)

_BURDEN_CATEGORY_ID = "burden"
_STABLE_LABEL = "Stable"
_STATE_SUPPRESSED = "suppressed"
_STATE_LIMITED = "limited"

#: Row height and chrome for every matrix on the page, so a 4-row and a 15-row matrix are the
#: same chart at two sizes rather than two different charts.
ROW_HEIGHT = 28
_CHROME_HEIGHT = 196
_MIN_HEIGHT = 240
#: Row labels are truncated to this, with the full name on hover. "Portable Circular Power Saw"
#: as a row label took 60% of a 390px viewport and left ~5px per column.
MAX_ROW_LABEL_CHARS = 16
#: Per-LINE budget for a rotated column label, which wraps to `MAX_COLUMN_LABEL_LINES` (see
#: `_wrap`). 20 chars on one line was the old whole-label ceiling and it clipped "Direct cutting
#: contact" to "Direct Cutting Cont…"; two lines of 16 carry 32 characters in a SHORTER band.
MAX_COLUMN_LABEL_CHARS = 16
MAX_COLUMN_LABEL_LINES = 2
#: Rotated column labels stand about this tall, and the coverage note sits under them.
_LABEL_BAND_PX = 96
_BOTTOM_MARGIN = _LABEL_BAND_PX + 44
#: Reserved band above the plot for the horizontal colorbar.
_TOP_MARGIN = 54

#: A wrap point is whitespace, or just after a "/" inside a compound ("Flying debris/foreign
#: body"). Without the slash rule a compound is one unbreakable token and gets truncated instead
#: of wrapped, which is the failure this whole function exists to remove.
_WRAP_TOKENS = re.compile(r"\S*?/|\S+")


def _short(label: str, max_chars: int) -> str:
    """Hard-truncate a label for an axis tick. Every full label stays reachable on hover."""
    return label if len(label) <= max_chars else label[: max_chars - 1].rstrip() + "…"


def _wrap(label: str, max_chars: int, max_lines: int = MAX_COLUMN_LABEL_LINES) -> str:
    """Break an axis tick over up to `max_lines` lines instead of truncating it.

    Rotated -45 degrees, a two-line label occupies LESS of the label band than the one-line
    version it replaces: a tick's band is roughly (width + height) x sin(45), and moving half the
    characters onto a second line takes far more off the width than the extra line adds to the
    height. That is why the wider labels fit without `_LABEL_BAND_PX` moving.

    Truncation survives as the fallback for a label that still overruns `max_lines` (and for a
    single token longer than `max_chars`), so the ellipsis becomes the exception rather than the
    rule it was for every column label over 20 characters.
    """
    if len(label) <= max_chars:
        return label
    lines: list[str] = []
    current = ""
    for token in _WRAP_TOKENS.findall(label):
        # No space after a "/" break -- "Flying debris/" + "foreign body", never "/ foreign".
        candidate = (
            token if not current else current + ("" if current.endswith("/") else " ") + token
        )
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = token
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = [*lines[: max_lines - 1], " ".join(lines[max_lines - 1 :])]
    return "<br>".join(_short(line, max_chars) for line in lines)


def _coverage_lines(suppressed: int, total_cells: int, hidden_columns: int) -> list[str]:
    """Why the grid is mostly empty, stated on the figure. Suppression is the MAJORITY state in
    these matrices (147 of 195 cells on the mechanism view at the default filter), so it is
    carried by absence plus this count rather than by a dashed outline per cell -- 147 dashed
    rectangles is graph paper, which is the amber-hatch failure in a different ink."""
    lines: list[str] = []
    if suppressed:
        lines.append(f"{suppressed} of {total_cells} cells are below the reporting floor (blank)")
    if hidden_columns:
        lines.append(f"{hidden_columns} columns with no reportable cell are not shown")
    return lines


_HOVERTEMPLATE = (
    "%{customdata[5]}, %{x}<br>Value: %{customdata[0]}<br>95% CI: %{customdata[1]}"
    "<br>Baseline: %{customdata[2]}<br>Raw N: %{customdata[3]} sample cases "
    "(not a national count)<br>%{customdata[4]}<extra></extra>"
)

#: log2 tick positions and the ratios they mean. The colorbar never shows a log unit.
_RATIO_TICKVALS = [-2.0, -1.0, 0.0, 1.0, 2.0]
_RATIO_TICKTEXT = ["¼×", "½×", "1×", "2×", "4×"]


def log2_ratio_capped(value: float, baseline: float) -> float | None:
    """log2(value / baseline), clamped to [-2, 2] (ratio 1.0 -> 0.0; 8x -> 2.0 capped;
    `value` <= 0 -> -2.0 capped, never -inf; `baseline` == 0 -> None, an undefined ratio that is
    never silently treated as 0)."""
    if baseline == 0:
        return None
    if value <= 0:
        return -2.0
    return max(-2.0, min(2.0, math.log2(value / baseline)))


def _cell_state(cell) -> str:
    if cell is None or cell.value is None:
        return _STATE_SUPPRESSED
    return "stable" if cell.stable else _STATE_LIMITED


def _cell_number(value: float | None) -> str:
    """The share as it is lettered INSIDE a cell: at most four characters, because a fingerprint
    cell is ~31px wide on a 390px sheet and "74.6%" needs 34. Ten percent and above rounds to a
    whole number ("75%"), below it keeps the decimal that carries the meaning ("0.7%"). Hover and
    the table view always carry the unrounded share and its CI."""
    if value is None:
        return marks.NOT_REPORTABLE_SHORT
    return f"{value * 100:.0f}%" if value >= 0.1 else f"{value * 100:.1f}%"


def _cell_text(state: str, display: str) -> str:
    """The number, or "N/R" (not reportable) when there is no number. The old "(limited)"
    suffix is gone: the dashed outline says it, in the channel reserved for saying it."""
    return marks.NOT_REPORTABLE_SHORT if state == _STATE_SUPPRESSED else display


def _state_label(state: str) -> str:
    if state == "stable":
        return _STABLE_LABEL
    if state == _STATE_LIMITED:
        return LIMITED_SAMPLE_LABEL
    return SUPPRESSED_LABEL


def _customdata_row(cell, baseline_value: float | None, state: str) -> list:
    """[value display, CI text, baseline display, raw N, stability label] -- one row per
    `_HOVERTEMPLATE`'s `%{customdata[i]}` reference: raw value, CI, the baseline it is compared
    against, raw N, stability."""
    value_text = marks.fmt_share(cell.value) if cell is not None else "Not reportable"
    if cell is not None and cell.ci_low is not None and cell.ci_high is not None:
        ci_text = f"{marks.fmt_share(cell.ci_low)}–{marks.fmt_share(cell.ci_high)}"
    else:
        ci_text = "Not reportable"
    baseline_text = marks.fmt_share(baseline_value)
    raw_n = cell.raw_n if cell is not None else 0
    return [value_text, ci_text, baseline_text, raw_n, _state_label(state)]


def _matrix_height(n_rows: int) -> int:
    return max(_MIN_HEIGHT, ROW_HEIGHT * n_rows + _CHROME_HEIGHT)


def build_fingerprint(
    dims: tuple[DimensionCategory, ...],
    tool_ids: tuple[str, ...],
    tool_labels: dict[str, str],
) -> go.Figure:
    """`dims` is `wii.app.data.comparison_metrics`'s output: burden first (drawn as the small
    sequential bar), then the ratio-to-baseline metrics (diverging, log2 capped +-2, zmid 0).
    `tool_ids` fixes row order and is bounded to 2-6 by the caller -- the builder never enforces
    the bound, it only ever receives an already-bounded selection.
    """
    burden_dim = next((d for d in dims if d.category_id == _BURDEN_CATEGORY_ID), None)
    ratio_dims = [d for d in dims if d.category_id != _BURDEN_CATEGORY_ID]
    # Truncated row labels: untruncated, "Portable Circular Power Saw" took half a 390px sheet and
    # left the matrix ~20px per column. Full names stay on hover.
    y_labels = [_short(tool_labels.get(tid, tid), MAX_ROW_LABEL_CHARS) for tid in tool_ids]
    full_labels = [tool_labels.get(tid, tid) for tid in tool_ids]

    fig = make_subplots(
        rows=1,
        cols=2,
        shared_yaxes=True,
        column_widths=[0.16, 0.84],
        # Wide enough that the burden bar's lettered value never runs into the matrix's first
        # column at 390px.
        horizontal_spacing=0.07,
    )

    # ---- the burden bar: a share, on the sequential ramp, beside the matrix rather than inside
    # it. Its own number is lettered past the bar end.
    burden_values: list[float] = []
    burden_states: list[str] = []
    burden_customdata: list[list] = []
    for tid in tool_ids:
        cell = burden_dim.by_tool.get(tid) if burden_dim is not None else None
        state = _cell_state(cell)
        burden_states.append(state)
        burden_values.append(cell.value if cell is not None and cell.value is not None else 0.0)
        burden_customdata.append(
            [*_customdata_row(cell, None, state), full_labels[len(burden_customdata)]]
        )

    burden_max = max(burden_values, default=0.0)
    fig.add_trace(
        go.Bar(
            x=burden_values,
            y=y_labels,
            orientation="h",
            marker={
                "color": [marks.fill_for(SEQUENTIAL_SCALE[3], state) for state in burden_states],
                "line": {
                    "color": GRAPHITE,
                    "width": [1 if s == "stable" else 0 for s in burden_states],
                },
            },
            text=[
                f" {_cell_number(v) if s != _STATE_SUPPRESSED else marks.NOT_REPORTABLE_SHORT}"
                for v, s in zip(burden_values, burden_states, strict=True)
            ],
            textposition="outside",
            cliponaxis=False,
            textfont={"size": CHART_DATA_LABEL_SIZE, "color": GRAPHITE},
            customdata=burden_customdata,
            hovertemplate=(
                "%{customdata[5]}: share of workshop burden<br>Value: %{customdata[0]}"
                "<br>95% CI: %{customdata[1]}<br>Raw N: %{customdata[3]} sample cases "
                "(not a national count)<br>%{customdata[4]}<extra></extra>"
            ),
            showlegend=False,
        ),
        row=1,
        col=1,
    )
    marks.add_confidence_outlines(fig, values=burden_values, states=burden_states)

    x_labels = [_wrap(d.category_label, MAX_COLUMN_LABEL_CHARS) for d in ratio_dims]
    z_matrix: list[list[float | None]] = []
    text_matrix: list[list[str]] = []
    customdata_matrix: list[list[list]] = []
    unstable_cells: list[tuple[int, int, str]] = []
    for y_index, tid in enumerate(tool_ids):
        z_row: list[float | None] = []
        text_row: list[str] = []
        cd_row: list[list] = []
        for x_index, dim in enumerate(ratio_dims):
            cell = dim.by_tool.get(tid)
            baseline_value = dim.baseline.value if dim.baseline is not None else None
            if cell is None or cell.value is None or baseline_value is None:
                z_row.append(None)
                state = _STATE_SUPPRESSED
            else:
                z_row.append(log2_ratio_capped(cell.value, baseline_value))
                state = "stable" if cell.stable else _STATE_LIMITED
            # LIMITED only. A suppressed cell already reads "N/R" in its own cell; boxing every
            # one of them in dashes makes the sheet graph paper and re-creates, in a new ink,
            # exactly the "we don't trust this" dominance the amber hatch had.
            if state == _STATE_LIMITED:
                unstable_cells.append((x_index, y_index, state))
            text_row.append(_cell_text(state, _cell_number(cell.value if cell else None)))
            cd_row.append([*_customdata_row(cell, baseline_value, state), full_labels[y_index]])
        z_matrix.append(z_row)
        text_matrix.append(text_row)
        customdata_matrix.append(cd_row)

    fig.add_trace(
        go.Heatmap(
            z=z_matrix,
            x=x_labels,
            y=y_labels,
            colorscale=DIVERGING_SCALE,
            zmid=0,
            zmin=-2,
            zmax=2,
            xgap=1,
            ygap=1,
            text=text_matrix,
            texttemplate="%{text}",
            textfont={"size": CHART_DATA_LABEL_SIZE},
            customdata=customdata_matrix,
            hovertemplate=_HOVERTEMPLATE,
            # Horizontal, above the plot: vertical it took width the matrix needs, and at 390px
            # its ticks sat on the cells.
            colorbar={
                "title": {"text": "Ratio to the all-workshop share", "side": "top"},
                "orientation": "h",
                "tickvals": _RATIO_TICKVALS,
                "ticktext": _RATIO_TICKTEXT,
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
        ),
        row=1,
        col=2,
    )
    marks.add_cell_outlines(fig, unstable_cells, xref="x2", yref="y2")

    fig.update_yaxes(autorange="reversed")
    style_chart(
        fig,
        height=_matrix_height(len(tool_ids)),
        margin={"l": 8, "r": 8, "t": _TOP_MARGIN, "b": _LABEL_BAND_PX},
        grid="none",
    )
    fig.update_xaxes(
        row=1,
        col=1,
        title_text="Burden",
        showticklabels=False,
        range=[0, (burden_max or 1.0) * 1.45],
    )
    fig.update_xaxes(
        row=1, col=2, tickangle=-45, tickfont={"size": TYPE_LABEL, "color": GRAPHITE_SOFT}
    )
    fig.update_yaxes(row=1, col=1, tickfont={"size": TYPE_LABEL, "color": GRAPHITE})
    return fig


# Share-matrix input frame columns (one row per tool_id x category): tool_id, tool_label,
# category, label, value (share, 0-1 or None), ci_low, ci_high, raw_n, state.


def drawn_categories(df: pl.DataFrame, category_order: list[str]) -> list[str]:
    """The categories with at least one reportable cell, plus any text-only column the caller
    keeps. A column that is empty for every row in view is not earning its width, and width is
    the whole legibility problem at 390px. Falls back to the full order when nothing is
    reportable, so the chart never renders without an x axis."""
    live = {
        row["category"]
        for row in df.iter_rows(named=True)
        if row["state"] != _STATE_SUPPRESSED and row["value"] is not None
    }
    kept = [c for c in category_order if c in live]
    return kept or list(category_order)


def build_share_matrix(
    df: pl.DataFrame,
    category_order: list[str],
    category_labels: dict[str, str] | None = None,
    *,
    zmax_fixed: float | None = None,
    text_only: tuple[str, ...] = (),
    colorbar_title: str = "Share",
    show_text: bool = False,
    drop_empty_categories: bool = True,
) -> go.Figure:
    """Generic within-group share matrix: rows = tools in the frame's own first-seen order,
    columns = `category_order`, z = within-tool share on the sequential ink ramp.

    One figure serves 1,440px and 390px, so it removes width demand rather than hoping for it:
    empty columns are dropped (and counted on the figure), column labels are rotated, row labels
    are truncated with the full name on hover, the colorbar is horizontal in the band above the
    plot (vertical, it stole the width the grid needs and its ticks landed on the cells at 390px),
    and in-cell numbers are off unless `show_text=True` -- a percentage in a 20px cell is a speck,
    and hover carries every number.

    A suppressed cell renders z=None (no fill, blank); a limited cell keeps its value under a
    dashed construction outline. Categories named in `text_only` are kept as a column but left
    OUT of the colour domain entirely (z=None with their number still printed) -- that is how
    "unknown" stops being the brightest column on the mechanism matrix and setting zmax for every
    real mechanism beside it. The colorbar is ticked as a percentage, never in bare 0-1 units.

    `zmax_fixed` pins the scale (the diagnosis signature's zmax=1); otherwise zmax is the
    maximum coloured value in the matrix, falling back to 1.0 so Plotly never gets a zero-width
    colour range.
    """
    if df.height == 0:
        return style_chart(go.Figure(go.Heatmap(z=[])), height=_MIN_HEIGHT, grid="none")

    all_categories = list(category_order)
    if drop_empty_categories:
        kept = drawn_categories(df, all_categories)
        category_order = [c for c in all_categories if c in set(kept) | set(text_only)]
    hidden_columns = len(all_categories) - len(category_order)
    suppressed_cells = 0

    tool_order = list(dict.fromkeys(df["tool_id"].to_list()))
    tool_labels = dict(zip(df["tool_id"], df["tool_label"], strict=True))
    label_by_category = (
        dict(category_labels)
        if category_labels is not None
        else dict(zip(df["category"], df["label"], strict=True))
    )

    z: list[list[float | None]] = []
    text: list[list[str]] = []
    customdata: list[list[list]] = []
    unstable_cells: list[tuple[int, int, str]] = []
    text_only_cells: list[tuple[int, int, str]] = []
    coloured_values: list[float] = []
    for y_index, tid in enumerate(tool_order):
        full_tool_label = tool_labels.get(tid, tid)
        tool_rows = {r["category"]: r for r in df.filter(pl.col("tool_id") == tid).to_dicts()}
        z_row: list[float | None] = []
        text_row: list[str] = []
        cd_row: list[list] = []
        for x_index, category in enumerate(category_order):
            row = tool_rows.get(category)
            if row is None or row["state"] == _STATE_SUPPRESSED or row["value"] is None:
                z_row.append(None)
                text_row.append(marks.NOT_REPORTABLE_SHORT)
                raw_n = row["raw_n"] if row is not None else 0
                cd_row.append(
                    [
                        marks.NOT_REPORTABLE,
                        marks.NOT_REPORTABLE,
                        raw_n,
                        SUPPRESSED_LABEL,
                        full_tool_label,
                    ]
                )
                suppressed_cells += 1
                continue
            display = marks.fmt_share(row["value"])
            state_label = _STABLE_LABEL if row["state"] == "stable" else LIMITED_SAMPLE_LABEL
            if category in text_only:
                z_row.append(None)
            else:
                z_row.append(row["value"])
                coloured_values.append(row["value"])
            text_row.append(display)
            if category in text_only:
                # A text-only column has no fill, so with in-cell numbers off it would be a blank
                # strip taking width. Its value is lettered per row instead, rounded to whole
                # percent so it fits a 22px cell on a phone.
                text_only_cells.append((x_index, y_index, f"{row['value'] * 100:.0f}%"))
            if row["state"] != "stable":
                unstable_cells.append((x_index, y_index, _STATE_LIMITED))
            if row["ci_low"] is not None and row["ci_high"] is not None:
                ci_text = f"{marks.fmt_share(row['ci_low'])}–{marks.fmt_share(row['ci_high'])}"
            else:
                ci_text = "Not reportable"
            cd_row.append([display, ci_text, row["raw_n"], state_label, full_tool_label])
        z.append(z_row)
        text.append(text_row)
        customdata.append(cd_row)

    zmax = zmax_fixed if zmax_fixed is not None else (max(coloured_values, default=1.0))

    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=[
                _wrap(label_by_category.get(c, c.replace("_", " ").title()), MAX_COLUMN_LABEL_CHARS)
                for c in category_order
            ],
            y=[_short(tool_labels.get(tid, tid), MAX_ROW_LABEL_CHARS) for tid in tool_order],
            colorscale=SEQUENTIAL_SCALE,
            zmin=0,
            zmax=zmax,
            xgap=1,
            ygap=1,
            text=text,
            texttemplate="%{text}" if show_text else None,
            textfont={"size": CHART_DATA_LABEL_SIZE},
            customdata=customdata,
            hovertemplate=(
                "%{customdata[4]}, %{x}<br>Share: %{customdata[0]}<br>95% CI: %{customdata[1]}"
                "<br>Raw N: %{customdata[2]} sample cases (not a national count)"
                "<br>%{customdata[3]}<extra></extra>"
            ),
            # Horizontal, in the band above the plot: vertical it took width the grid needs,
            # and at 390px its tick labels sat on top of the cells.
            colorbar={
                "title": {"text": colorbar_title, "side": "top"},
                "orientation": "h",
                "tickformat": ".0%",
                "tickvals": [0.0, zmax / 2, zmax],
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
    # Only LIMITED cells take the dashed construction outline; a suppressed cell is bare and the
    # note below counts them (see `_coverage_line`).
    marks.add_cell_outlines(fig, unstable_cells)
    fig.update_yaxes(autorange="reversed")
    style_chart(
        fig,
        height=_matrix_height(len(tool_order)),
        margin={"l": 8, "r": 8, "t": _TOP_MARGIN, "b": _BOTTOM_MARGIN},
        grid="none",
    )
    lines = _coverage_lines(suppressed_cells, len(tool_order) * len(category_order), hidden_columns)
    if lines:
        fig.add_annotation(
            **chart_note(
                "<br>".join(lines),
                xref="paper",
                # Anchored to the plot's RIGHT edge so the text runs leftwards across the figure,
                # over the empty band under the row labels; anchored left it would start at the
                # plot's left edge and run off a 390px sheet.
                x=1,
                xanchor="right",
                align="left",
                yref="paper",
                y=0,
                yanchor="top",
                yshift=-_LABEL_BAND_PX,
            )
        )
    for x_index, y_index, label in text_only_cells:
        fig.add_annotation(
            text=label,
            x=x_index,
            y=y_index,
            xref="x",
            yref="y",
            showarrow=False,
            font={"family": FONT_SANS, "size": CHART_DATA_LABEL_SIZE, "color": GRAPHITE_SOFT},
        )
    fig.update_xaxes(tickangle=-45, tickfont={"size": TYPE_LABEL, "color": GRAPHITE_SOFT})
    fig.update_yaxes(tickfont={"size": TYPE_LABEL, "color": GRAPHITE})
    return fig


def build_diagnosis_signature(df: pl.DataFrame) -> go.Figure:
    """Rows = tools in the frame's own first-seen order; columns = the fixed 9 diagnosis palette
    keys, z = within-tool primary diagnosis share with zmax pinned at 1 so shares stay comparable
    across renders."""
    return build_share_matrix(df, list(DIAGNOSIS_COLORS), zmax_fixed=1)


def build_mechanism_heatmap(df: pl.DataFrame) -> go.Figure:
    """MECHANISM BY TOOL: columns follow `theme.MECHANISM_COLORS`' fixed key order, which ends
    with "unknown" (asserted below); row order is the frame's own `tool_id` order (already
    stable-first ranked by `wii.app.data.mechanism_tool_share_frame`).

    "unknown" is drawn as a text-only column: it is narrative-coverage bookkeeping rather than a
    mechanism, it is routinely the largest value in a row, and colouring it made it the brightest
    column on the chart and pushed every real mechanism to the bottom of the scale.
    """
    assert list(MECHANISM_COLORS)[-1] == "unknown"
    category_order = list(MECHANISM_COLORS)
    category_labels = {key: mechanism_label(key) for key in category_order}
    return build_share_matrix(
        df,
        category_order,
        category_labels,
        text_only=("unknown",),
        colorbar_title="Share of<br>this tool's cases",
    )
