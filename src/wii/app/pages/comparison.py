"""PAGE-05 Tool Comparison (D-11). Owns its OWN tool selection under the
`comparison_selected_tools` session-state key -- distinct from the global sidebar's
`global_tool_filter` key and Tool Profiles' `profile_selected_tool` key, which app.py hides on
this page (D-03, `_HIDE_TOOL_PAGES`). Section order per UI-SPEC: title -> multiselect (2-6,
computed default) -> bound empty states -> INJURY FINGERPRINT (chart, or the same numbers as a
table behind a toggle) -> ANATOMY SIGNATURE -> HOW THEY DIFFER -> footer (rendered globally by
app.py). The page said everything twice before: the strip printed the fingerprint's own in-cell
numbers, and the diagnosis signature redrew the fingerprint's diagnosis columns on a scale pinned
at 1. One fingerprint, one anatomy row, one set of pairwise findings.
"""

from __future__ import annotations

import html
from dataclasses import replace

import streamlit as st

from wii.analytics.insights import compare_tools, load_thresholds
from wii.app import filters, theme
from wii.app.charts import heatmap
from wii.app.components.body_map import (
    VIEWS_FRONT,
    BodyScale,
    RegionValue,
    callout_row_note,
    render_body_map,
)
from wii.app.components.filter_context import render_filter_context, render_title_block
from wii.app.components.insight_card import render_insight_section
from wii.app.components.stability_badge import STATE_STABLE, format_share, resolve
from wii.app.data import (
    anatomy_snapshot_frame,
    cached_estimate_by,
    comparison_metrics,
    insight_frames,
    load_dims,
)
from wii.metrics import CaseFilter

_ANATOMY_SIGNATURE_SUBTITLE = (
    "Front view only, and all bodies share one colour scale, so a region can be compared across "
    "tools rather than only within one."
)
_HOW_THEY_DIFFER_CAPTION = (
    "Deterministic differences that clear the same stability and effect-size thresholds as "
    "WHAT THE DATA IS SAYING -- never padded with weaker statements."
)
_TABLE_TOGGLE_LABEL = "Table view"
_TABLE_TOGGLE_HELP = "The same nine numbers as the fingerprint, as text."
#: "Maintenance/setup" was ~0.0% and limited for every tool on every render -- a column that only
#: ever said "no reportable difference". It stays on the Mechanisms page, where it has context.
_DROPPED_METRIC_IDS = frozenset({"maintenance_setup"})
#: Lower is the better direction for every metric here: these are injury shares, so a tool sitting
#: BELOW the workshop baseline on a column is the favourable reading. Said in words because
#: colour alone must never carry direction.
_DIRECTION_NOTE = (
    "Every column is an injury share, so a tool below the workshop baseline (cooler) is the "
    "lower-burden reading and above it (warmer) is the higher one."
)

_TITLE = "Every tool has an injury fingerprint"
#: The title block's own line. The sidebar's tool filter is hidden on this page, so the strip is
#: rendered with `tools_override=()` -- the workshop-wide filter these comparisons are made
#: against -- and the page's own selection count is stated in the scope field.
DRAWING = "Selected tools compared against the workshop"
_MULTISELECT_LABEL = "Compare tools (2–6)"
_MIN_TOOLS = 2
_MAX_TOOLS = 6
_DEFAULT_TOOL_COUNT = 4

_UNDER_MIN_HEADING = "Select at least 2 tools to compare."
_UNDER_MIN_BODY = (
    "Choose 2 to 6 tools from the list above to see the injury fingerprint, the anatomy "
    "signature and how they differ."
)


def _over_max_copy(n_selected: int) -> str:
    return (
        "Select up to 6 tools; comparisons beyond 6 become hard to read. Remove "
        f"{n_selected - _MAX_TOOLS} tool(s) to continue."
    )


def _default_tool_ids(flt: CaseFilter, basis: str, options: list[str]) -> tuple[str, ...]:
    """D-11 backstop: the `_DEFAULT_TOOL_COUNT` highest basis-matched estimates among STABLE
    tools under `flt`, never hard-coded ids. Ties broken by tool_id ascending, mirroring
    `data.default_tool_id`'s D-10 backstop."""
    df = cached_estimate_by(flt, "tool")
    scored: list[tuple[float, str]] = []
    for row in df.iter_rows(named=True):
        resolved = resolve(row, basis)
        if resolved.state != STATE_STABLE or resolved.value is None:
            continue
        scored.append((resolved.value, row["group_id"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(tool_id for _, tool_id in scored[:_DEFAULT_TOOL_COUNT] if tool_id in options)


def _ensure_default_selection(options: list[str]) -> None:
    current = st.session_state.get("comparison_selected_tools")
    if current is not None and all(tid in options for tid in current):
        return
    basis = st.session_state.get("basis", "annual_average")
    workshop_flt = filters.current_filter(tools_override=())
    st.session_state["comparison_selected_tools"] = list(
        _default_tool_ids(workshop_flt, basis, options)
    )


def _cell_html(cell) -> str:
    if cell is None or cell.value is None:
        return '<span class="wii-comparison-suppressed">Not reportable</span>'
    display = html.escape(format_share(cell.value))
    if not cell.stable:
        return f'<span>{display}</span><span class="wii-comparison-flag">Limited</span>'
    return f"<span>{display}</span>"


def _render_comparison_strip(dims, tool_ids: tuple[str, ...], tool_labels: dict[str, str]) -> None:
    """The fingerprint's table twin. It is no longer a section of its own: it printed the same
    36 values the heatmap directly below it already carried in every cell, within one screen."""
    header_cells = "".join(f"<th>{html.escape(d.category_label)}</th>" for d in dims)
    rows_html: list[str] = []
    for tool_id in tool_ids:
        label = html.escape(tool_labels.get(tool_id, tool_id))
        cells = "".join(f"<td>{_cell_html(d.by_tool.get(tool_id))}</td>" for d in dims)
        rows_html.append(f"<tr><th>{label}</th>{cells}</tr>")
    table = (
        '<table class="wii-comparison-strip">'
        f"<thead><tr><th>Tool</th>{header_cells}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        "</table>"
    )
    st.markdown(table, unsafe_allow_html=True)


def _render_fingerprint(dims, tool_ids: tuple[str, ...], tool_labels: dict[str, str]) -> None:
    """One fingerprint, in one of two views. The chart is the default; the toggle swaps in the
    same numbers as a table for a reader who wants to read rather than scan."""
    st.header("Injury fingerprint")
    st.caption(heatmap.FINGERPRINT_CAPTION)
    st.caption(_DIRECTION_NOTE)
    as_table = st.toggle(_TABLE_TOGGLE_LABEL, key="comparison_table_view", help=_TABLE_TOGGLE_HELP)
    if as_table:
        _render_comparison_strip(dims, tool_ids, tool_labels)
        return
    fig = heatmap.build_fingerprint(dims, tool_ids, tool_labels)
    st.plotly_chart(fig, config=theme.PLOTLY_CONFIG, width="stretch")


def _shared_anatomy_scale(
    values_by_tool: dict[str, dict[str, RegionValue]], region_ids: set[str]
) -> BodyScale:
    """D-11/D-12 hard requirement: ONE `BodyScale` (vmax = the max within-tool region share
    across ALL selected tools) passed identically into every `render_body_map` call -- never
    independently autoscaled per tool."""
    candidates = [
        rv.value
        for values in values_by_tool.values()
        for rid, rv in values.items()
        if rid in region_ids and rv.value is not None
    ]
    vmax = max(candidates) if candidates else 0.0
    return BodyScale(vmin=0.0, vmax=vmax if vmax > 0 else 1.0)


def _render_anatomy_signature(
    flt: CaseFilter, basis: str, tool_ids: tuple[str, ...], tool_labels: dict[str, str]
) -> None:
    st.header("Anatomy signature")
    st.caption(_ANATOMY_SIGNATURE_SUBTITLE)
    dims = load_dims()["body_regions_all"]
    region_ids = {row["region_id"] for row in dims.iter_rows(named=True) if row["kind"] == "region"}
    values_by_tool = {
        tool_id: anatomy_snapshot_frame(replace(flt, tools=(tool_id,)), basis)
        for tool_id in tool_ids
    }
    scale = _shared_anatomy_scale(values_by_tool, region_ids)
    _row_note = callout_row_note(list(values_by_tool.values()))
    cols = st.columns(len(tool_ids))
    for index, (col, tool_id) in enumerate(zip(cols, tool_ids, strict=True)):
        with col:
            st.markdown(f"**{tool_labels.get(tool_id, tool_id)}**")
            # A small-multiple row, so everything that repeats per figure is drawn once for the
            # row instead: front view only (four tools x front+back was eight silhouettes down
            # 1,200px for a finding one region wide), one key, and no per-region dashes. At
            # ~140px a side the dashes outnumbered the filled regions 8.5:1 and read as texture
            # rather than as a line treatment -- fill is the only variable at this size, and
            # every region still names its own stability in its `<title>`.
            render_body_map(
                values_by_tool[tool_id],
                scale,
                instance_id=f"comparison-{tool_id}",
                views=VIEWS_FRONT,
                region_ids=region_ids,
                confidence_outlines=False,
                legend=index == 0,
                callouts=_row_note == "",
            )
    # The four callout categories carry no `<path>`, so they print as chips beside each figure.
    # When every one of them is suppressed for every tool -- the usual case -- that is the same
    # four-item list of dashes under all four figures, so it is said once here instead. The note
    # returns "" the moment any of them has a value, and the per-figure chips come back.
    if _row_note:
        st.caption(_row_note)


def _render_how_they_differ(
    workshop_flt: CaseFilter, basis: str, tool_ids: tuple[str, ...]
) -> None:
    st.header("How they differ")
    st.caption(_HOW_THEY_DIFFER_CAPTION)
    thresholds = load_thresholds()
    frames = insight_frames(workshop_flt, basis)
    insights = compare_tools(frames, tool_ids, thresholds, basis)
    render_insight_section(insights, thresholds.min_cards_for_section)


def render() -> None:
    tools_dim = load_dims()["tools"]
    options = tools_dim["tool_id"].to_list()
    tool_labels = dict(zip(tools_dim["tool_id"], tools_dim["label"], strict=True))
    if not options:
        st.warning("No tools are available in the committed dataset.")
        return

    st.title(_TITLE)
    _ensure_default_selection(options)
    st.multiselect(
        _MULTISELECT_LABEL,
        options=options,
        format_func=lambda tid: tool_labels.get(tid, tid),
        key="comparison_selected_tools",
    )
    selected = tuple(st.session_state["comparison_selected_tools"])

    if len(selected) < _MIN_TOOLS:
        st.info(f"{_UNDER_MIN_HEADING} {_UNDER_MIN_BODY}")
        return
    if len(selected) > _MAX_TOOLS:
        st.info(_over_max_copy(len(selected)))
        return

    basis = st.session_state.get("basis", "annual_average")
    render_title_block(
        DRAWING,
        tools_override=(),
        scope_extra=f"{len(selected)} tools compared",
    )
    # The page owns its tool selection, so the chip row is scoped the way the queries are
    # (`tools_override=()`), and it appears only when a sidebar filter is actually set -- the
    # strip above already states basis, scope and source.
    render_filter_context(tools_override=(), only_when_active=True)
    workshop_flt = filters.current_filter(tools_override=())
    dims = tuple(
        d
        for d in comparison_metrics(workshop_flt, selected, basis)
        if d.category_id not in _DROPPED_METRIC_IDS
    )
    # The diagnosis signature is gone: its nine columns were the fingerprint's diagnosis columns
    # drawn a second time, on a scale pinned at 1 that left eight of them nearly blank.
    _render_fingerprint(dims, selected, tool_labels)
    _render_anatomy_signature(workshop_flt, basis, selected, tool_labels)
    _render_how_they_differ(workshop_flt, basis, selected)


render()
