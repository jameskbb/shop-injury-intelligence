"""PAGE-04 Mechanisms (D-08/D-09/D-10/D-11): five coverage/mechanism KPIs and the INJURY PATHWAYS
Sankey (Tool -> Primary mechanism -> Primary body region -> Diagnosis category -> Disposition),
built entirely from already-estimated `wii.metrics.estimate_by_pair` rows -- this page and
`wii.app.charts.sankey` only collapse/format/caption, never re-derive a weighted estimate
(D-11/T-04-20). Unlike Body Map/Tool Profiles/Comparison, this page is NOT added to
`_HIDE_TOOL_PAGES`: the global sidebar tool filter applies normally; only the Sankey's own
single-tool focus selector (Task 2) is page-local.
"""

from __future__ import annotations

import html
from dataclasses import replace

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from wii.analytics.insights import load_thresholds
from wii.analytics.mechanism_trend import load_trend_gate
from wii.app import filters, theme
from wii.app.charts import heatmap, marks, profile_bars, ranked_bars, sankey, trend
from wii.app.components import stability_badge
from wii.app.components.filter_context import render_filter_context, render_title_block
from wii.app.components.kpi_card import KpiCard, render_kpi_strip
from wii.app.components.narrative_card import narrative_list_html
from wii.app.components.stability_badge import empty_state, resolve_share
from wii.app.data import (
    available_years,
    cached_estimate_total,
    filtered_narratives,
    load_dims,
    maintenance_context_frame,
    maintenance_share_by_tool_frame,
    mechanism_kpi_rows,
    mechanism_profile_frame,
    mechanism_tool_share_frame,
    mechanism_trend_frame,
    power_state_frame,
    sankey_data,
)
from wii.metrics import CaseFilter

PAGE_TITLE = "How workshop injuries happen"
#: The one line the title block states about this sheet. Every analytical page opens with the
#: strip: basis, scope and data vintage belong above the fold, not scattered through captions.
DRAWING = "How workshop injuries happen: narrative-derived"
PAGE_SUBTITLE = (
    "Narrative-derived mechanism shares and a five-stage injury pathway -- how workshop injuries "
    "happen, not how likely they are."
)

SANKEY_SUBTITLE = (
    "Each case is drawn once, along the path its own record describes: tool, mechanism, body "
    "region, diagnosis, outcome. A band's width is how many estimated injuries followed that "
    "path; the diagram shows what was recorded together, not what caused what."
)
#: One sentence for what the diagram drops, replacing five stacked caption lines (one of which
#: printed a bare dash). Merged and hidden stay SEPARATE facts -- merged volume is still on the
#: diagram inside "Other", hidden volume is not drawn at all.
VOLUME_CAPTION_TEMPLATE = (
    "{merged} estimated injuries are merged into 'Other' links here; {hidden} more fall below "
    "the reporting floor and are not drawn at all."
)
MERGED_ONLY_TEMPLATE = "{merged} estimated injuries are merged into 'Other' links in this view."
HIDDEN_ONLY_TEMPLATE = (
    "{hidden} estimated injuries fall below the reporting floor and are not drawn, not even "
    "inside 'Other'."
)
NOT_RECORDED_CAPTION = (
    "Cases with a 'not recorded' disposition are excluded from this flow. Each case is counted "
    "under the first in-scope tool its narrative names."
)
EXCLUDED_VOLUME_TEMPLATE = "Excluded volume: {volume}."
EMPTY_SANKEY_NOTE = (
    "No pathway links meet the stability threshold for this filter selection -- try widening "
    "the year range or filters."
)
FOCUS_ALL_LABEL = "All tools (top 6 + Other)"

MECHANISM_HEATMAP_SUBTITLE = (
    "Each row is normalized within its tool, so mechanism signatures stay comparable regardless "
    "of volume."
)
# Same verbatim wording as body_map.py's MECHANISM_PROVENANCE_CAPTION (D-12's mechanism heatmap
# is the same narrative-derived classification as the Body Map's mechanism selector).
MECHANISM_PROVENANCE_CAPTION = (
    "Mechanism categories are narrative-derived (mined from ER visit narratives), not a coded "
    "NEISS field."
)
EMPTY_HEATMAP_NOTE = (
    "No tools have data for this filter selection -- try widening the year range or filters."
)
#: The matrix is ranked stable-first by `mechanism_tool_share_frame`, so the tail rows are the
#: ones with nothing stable to show. Fifteen rows is the readable default; the toggle keeps every
#: tool reachable rather than dropping them.
_HEATMAP_TOP_TOOLS = 15
_HEATMAP_TOGGLE_LABEL = "Show all tools"
_HEATMAP_SCOPE_TEMPLATE = "Showing the {n} highest-burden tools with reportable mechanism shares."
_HEATMAP_SCOPE_ALL = "Showing every tool with reportable mechanism shares."

MECHANISM_SHARES_HEADING = "Mechanism shares across the workshop"
MECHANISM_SHARES_CAPTION = (
    "Share of filtered cases associated with each mechanism, narrative-derived. Categories below "
    "the reporting floor are not drawn; a dashed outline marks a limited sample."
)
MECHANISM_SHARES_EMPTY = (
    "No mechanism clears the reporting floor for this filter selection, so no shares are drawn."
)

MECHANISM_TREND_SHOWN_CAPTION_TEMPLATE = (
    "Per-year primary-mechanism shares, 2019–{latest}. Years with unusually high unknown-"
    "mechanism rates are omitted; gaps mean insufficient classification consistency, not zero "
    "injuries."
)
MECHANISM_TREND_INSUFFICIENT_TEMPLATE = (
    "Fewer than {min_years} years in the 2019–{latest} window meet the classification-consistency "
    "threshold for this filter selection; showing no trend rather than a misleading one."
)

MAINTENANCE_INTRO = (
    "Narrative-derived context for maintenance and setup injuries, shown only where the "
    "narrative explicitly documents it; absence of a mention is not evidence a step was skipped."
)
MAINTENANCE_CONTEXT_SUBTITLE = (
    "Estimated maintenance/setup injuries by the activity the narrative documents "
    "(narrative-derived)."
)
MAINTENANCE_SHARE_BY_TOOL_CAPTION = (
    "Share of each tool's ER cases whose narrative documents maintenance or setup "
    "(narrative-derived)."
)
POWER_STATE_CAPTION = (
    "Among maintenance and setup cases. Most narratives do not mention power state; that is "
    "expected and says nothing about what happened."
)
MAINTENANCE_EMPTY_COPY = (
    "No narrative-documented maintenance or setup cases match the current filters."
)
MAINTENANCE_NO_NARRATIVES_COPY = (
    "No high-confidence narrative examples available for maintenance or setup cases under the "
    "current filters."
)
MAINTENANCE_TOP_TOOLS = 10
#: The section header carries the share, so a reader knows its size before opening it.
MAINTENANCE_SECTION_LABEL = "Setup & maintenance: {share} of filtered cases"
MAINTENANCE_SECTION_LABEL_UNKNOWN = "Setup & maintenance"
MAINTENANCE_LIST_LIMITED_DIVIDER = "Limited sample: below the stability threshold"

#: One KPI. The four mechanism-share cards it replaces are now bars in
#: `MECHANISM_SHARES_HEADING`, where each share can be read against the others and against the
#: whole; as isolated cards they had no comparator at all.
_COVERAGE_KPI_LABEL = "Classified narrative coverage"
_COVERAGE_KPI_SUFFIX = "with a known primary mechanism"
#: `mechanism_kpi_rows` returns 5 rows in a fixed order; coverage is the first.
_COVERAGE_ROW_INDEX = 0
_MAINTENANCE_ROW_INDEX = 4


def _kpi_card(label: str, suffix: str, row: dict) -> KpiCard:
    resolved = resolve_share(row)
    caption = f"Share of filtered cases {suffix}, narrative-derived"
    if resolved.label:
        caption = f"{caption} ({resolved.label})"
    return KpiCard(label=label, value=resolved.display_value, caption=caption)


def _sync(widget_key: str, persist_key: str) -> None:
    st.session_state[persist_key] = st.session_state[widget_key]


def _focus_options() -> tuple[list[str], dict[str, str]]:
    tools = load_dims()["tools"]
    options = [FOCUS_ALL_LABEL, *tools["tool_id"].to_list()]
    labels = {FOCUS_ALL_LABEL: FOCUS_ALL_LABEL}
    labels.update(dict(zip(tools["tool_id"], tools["label"], strict=True)))
    return options, labels


def _render_focus_selector() -> str | None:
    """D-10 single-tool focus selector -- persistent/widget key split (Wave-0 spike A5 finding,
    matches `filters.py`'s pattern): reading the widget key directly would lose the selection the
    moment Streamlit prunes it from `session_state` on a rerun that doesn't render this widget."""
    options, labels = _focus_options()
    st.session_state.setdefault("mech_focus_tool", FOCUS_ALL_LABEL)
    if "widget_mech_focus_tool" not in st.session_state:
        st.session_state["widget_mech_focus_tool"] = st.session_state["mech_focus_tool"]
    st.selectbox(
        "Focus on one tool",
        options=options,
        format_func=lambda tid: labels.get(tid, tid),
        key="widget_mech_focus_tool",
        on_change=_sync,
        args=("widget_mech_focus_tool", "mech_focus_tool"),
    )
    selected = st.session_state["mech_focus_tool"]
    return None if selected == FOCUS_ALL_LABEL else selected


def _maintenance_share_bars(rows: list[dict]) -> go.Figure | None:
    """ "Maintenance/setup share by tool" bars: one horizontal bar per tool, top
    `MAINTENANCE_TOP_TOOLS` rows -- `rows` are already stable-first ordered and resolved by
    `maintenance_share_by_tool_frame`.

    Returns `None` when NO row is stable. Ten dashed bars at 0-3% is a barcode that says "nothing
    here is stable" in the loudest available channel; `_maintenance_share_list_html` says the same
    thing in one readable list instead (MECH-11).
    """
    rows = [r for r in rows[:MAINTENANCE_TOP_TOOLS] if r["value"] is not None]
    if not any(r["state"] == stability_badge.STATE_STABLE for r in rows):
        return None
    states = [r["state"] for r in rows]
    values = [r["value"] for r in rows]
    fig = go.Figure(
        go.Bar(
            x=values,
            y=[r["label"] for r in rows],
            orientation="h",
            text=[f" {r['display_value']}" for r in rows],
            textposition="outside",
            cliponaxis=False,
            textfont={"size": theme.CHART_DATA_LABEL_SIZE, "color": theme.GRAPHITE},
            marker={
                "color": [marks.fill_for(theme.PRIMARY, s) for s in states],
                "line": {
                    "color": theme.GRAPHITE,
                    "width": [1 if s == stability_badge.STATE_STABLE else 0 for s in states],
                },
            },
            hovertext=[
                f"{r['label']}<br>Share: {r['display_value']}"
                f"<br>{stability_badge.raw_n_label(r['raw_n'])}"
                for r in rows
            ],
            hoverinfo="text",
        )
    )
    fig.update_layout(bargap=0.3)
    marks.add_confidence_outlines(fig, values=values, states=states)
    theme.style_chart(
        fig,
        height=max(160, 26 * len(rows) + 56),
        margin={"l": 8, "r": 56, "t": 8, "b": 36},
        grid="x",
    )
    fig.update_xaxes(
        title_text="Share of that tool's ER cases",
        tickformat=".0%",
        range=[0, max(values) * 1.25],
    )
    fig.update_yaxes(autorange="reversed", tickfont={"color": theme.GRAPHITE})
    return fig


def _maintenance_share_list_html(rows: list[dict]) -> str:
    """The ranked-list form My Shop uses, for when no tool's maintenance/setup share is stable:
    a stable block, then a labelled dashed divider, then the limited rows. Suppressed rows are
    counted in the caption by the caller rather than printed as a column of dashes."""
    items: list[str] = []
    divider_written = False
    for row in rows[:MAINTENANCE_TOP_TOOLS]:
        if row["state"] == stability_badge.STATE_SUPPRESSED:
            continue
        if row["state"] == stability_badge.STATE_LIMITED and not divider_written:
            items.append(
                f'<div class="wii-limited-divider">{html.escape(MAINTENANCE_LIST_LIMITED_DIVIDER)}'
                "</div>"
            )
            divider_written = True
        label = html.escape(row["label"])
        value = html.escape(row["display_value"])
        raw_n = html.escape(stability_badge.raw_n_label(row["raw_n"]))
        items.append(
            f'<div class="wii-shop-list-row">{label}: {value} <span>({raw_n})</span></div>'
        )
    return f'<div class="wii-shop-list">{"".join(items)}</div>'


def _power_state_table_html(rows: list[dict]) -> str:
    """A compact HTML table -- every interpolated value is `html.escape`d (T-04-27); no blank
    line ever appears inside the injected HTML string."""
    body_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row['label'])}</td>"
        f"<td>{html.escape(row['display_value'])}</td>"
        f"<td>{html.escape(stability_badge.raw_n_label(row['raw_n']))}</td>"
        "</tr>"
        for row in rows
    )
    return f'<table class="wii-power-state-table"><tbody>{body_rows}</tbody></table>'


def _render_setup_maintenance(flt: CaseFilter, basis: str, share_label: str | None) -> None:
    """One expander, not four sections. Maintenance/setup is a fraction of a percent of cases; as
    an open section it was ~40% of the page's height for a mechanism the data can barely support,
    and it opened with a chart in which every row was suppressed. The section still exists, still
    carries every caveat, and now states its own size on the way in.
    """
    label = (
        MAINTENANCE_SECTION_LABEL.format(share=share_label)
        if share_label
        else MAINTENANCE_SECTION_LABEL_UNKNOWN
    )
    with st.expander(label, expanded=False):
        st.caption(MAINTENANCE_INTRO)

        maint_flt = replace(flt, maintenance_or_setup=True)
        overall_maint = cached_estimate_total(maint_flt)
        if overall_maint is None or overall_maint.raw_n == 0:
            st.info(MAINTENANCE_EMPTY_COPY)
            return

        st.markdown("#### Maintenance context")
        st.caption(MAINTENANCE_CONTEXT_SUBTITLE)
        context_df = maintenance_context_frame(flt, basis)
        if context_df.height == 0:
            st.info(MAINTENANCE_EMPTY_COPY)
        else:
            fig = ranked_bars.build_ranked_bars(context_df, basis, "Estimated injuries")
            if fig is None:
                # Every activity is below the reporting floor. Drawing the chart anyway gives a
                # zero-width value axis, which Plotly autoranges and labels in SI milli-units
                # beside a column of dashes; one honest line says strictly more (MECH-10).
                st.info(ranked_bars.all_suppressed_note(context_df, basis, "activities"))
            else:
                st.plotly_chart(fig, config=theme.PLOTLY_CONFIG, width="stretch")
                footer = ranked_bars.suppressed_footer_note(context_df, basis)
                if footer:
                    st.caption(footer)
            if flt.years:
                st.caption(stability_badge.basis_caption(basis, flt.years[0], flt.years[1]))

        st.markdown("#### Maintenance/setup share by tool")
        st.caption(MAINTENANCE_SHARE_BY_TOOL_CAPTION)
        share_df = maintenance_share_by_tool_frame(flt)
        if share_df.height == 0:
            st.info(MAINTENANCE_EMPTY_COPY)
        else:
            share_rows = list(share_df.iter_rows(named=True))
            share_fig = _maintenance_share_bars(share_rows)
            if share_fig is None:
                st.markdown(_maintenance_share_list_html(share_rows), unsafe_allow_html=True)
                suppressed = sum(
                    1 for r in share_rows if r["state"] == stability_badge.STATE_SUPPRESSED
                )
                if suppressed:
                    st.caption(f"{suppressed} further tool(s) are below the reporting floor.")
            else:
                st.plotly_chart(share_fig, config=theme.PLOTLY_CONFIG, width="stretch")

        st.markdown("#### Power-state mentions")
        st.caption(POWER_STATE_CAPTION)
        power_rows = list(power_state_frame(flt, basis).iter_rows(named=True))
        st.markdown(_power_state_table_html(power_rows), unsafe_allow_html=True)

        st.markdown("#### Representative maintenance narratives")
        thresholds = load_thresholds()
        narratives = filtered_narratives(maint_flt, thresholds.narrative_min_confidence, 5)
        if narratives.height == 0:
            st.info(MAINTENANCE_NO_NARRATIVES_COPY)
        else:
            st.markdown(
                narrative_list_html(narratives.iter_rows(named=True)), unsafe_allow_html=True
            )


def _volume_caption(merged: float, hidden: float) -> str | None:
    """The one disclosure sentence under the diagram, or None when it drops nothing."""
    merged_text = stability_badge.format_estimate(merged)
    hidden_text = stability_badge.format_estimate(hidden)
    if merged > 0 and hidden > 0:
        return VOLUME_CAPTION_TEMPLATE.format(merged=merged_text, hidden=hidden_text)
    if merged > 0:
        return MERGED_ONLY_TEMPLATE.format(merged=merged_text)
    if hidden > 0:
        return HIDDEN_ONLY_TEMPLATE.format(hidden=hidden_text)
    return None


def _render_mechanism_shares(flt: CaseFilter) -> None:
    """MECH-02..05 as bars rather than four isolated cards: 43.4% / 3.4% / 15.2% / 0.3% with no
    comparator told a reader nothing about the whole, and the same four numbers were already the
    first row of the matrix below."""
    st.header(MECHANISM_SHARES_HEADING)
    st.caption(MECHANISM_SHARES_CAPTION)
    frame = mechanism_profile_frame(flt)
    fig = profile_bars.build_mechanism_bars(frame, drop_when_empty=True)
    if fig is None:
        st.info(MECHANISM_SHARES_EMPTY)
        return
    st.plotly_chart(fig, config=theme.PLOTLY_CONFIG, width="stretch")
    st.caption(MECHANISM_PROVENANCE_CAPTION)


def _render_mechanism_matrix(flt: CaseFilter, basis: str) -> None:
    st.header("Mechanism by tool")
    st.caption(MECHANISM_HEATMAP_SUBTITLE)
    heatmap_df = mechanism_tool_share_frame(flt, basis)
    n_tools = heatmap_df["tool_id"].n_unique() if heatmap_df.height else 0
    if n_tools == 0:
        st.info(EMPTY_HEATMAP_NOTE)
        return
    show_all = st.toggle(_HEATMAP_TOGGLE_LABEL, key="mech_heatmap_show_all")
    if not show_all and n_tools > _HEATMAP_TOP_TOOLS:
        kept = list(dict.fromkeys(heatmap_df["tool_id"].to_list()))[:_HEATMAP_TOP_TOOLS]
        heatmap_df = heatmap_df.filter(pl.col("tool_id").is_in(kept))
        scope = _HEATMAP_SCOPE_TEMPLATE.format(n=len(kept))
    else:
        scope = _HEATMAP_SCOPE_ALL
    st.plotly_chart(
        heatmap.build_mechanism_heatmap(heatmap_df),
        config=theme.PLOTLY_CONFIG,
        width="stretch",
    )
    st.caption(f"{scope} {heatmap.MECHANISM_HEATMAP_CAPTION}")


def render() -> None:
    st.title(PAGE_TITLE)
    st.caption(PAGE_SUBTITLE)
    render_title_block(DRAWING)
    # Only when something is actually filtered: the strip above already states basis, scope and
    # source, so on a default view the chip row would restate all three directly beneath it.
    render_filter_context(only_when_active=True)

    flt = filters.current_filter()
    overall = cached_estimate_total(flt)
    if overall is None or overall.raw_n == 0:
        empty_state(filters.filter_summary())
        return

    basis = st.session_state.get("basis", "annual_average")

    # The focus selector sits with the page controls, not inside the chart section it steers --
    # a per-chart filter buried mid-page is the anti-pattern the review called out.
    focus_tool = _render_focus_selector()

    kpi_rows = mechanism_kpi_rows(flt)
    render_kpi_strip(
        [_kpi_card(_COVERAGE_KPI_LABEL, _COVERAGE_KPI_SUFFIX, kpi_rows[_COVERAGE_ROW_INDEX])]
    )

    _render_mechanism_shares(flt)

    st.header("Injury pathways")
    st.caption(SANKEY_SUBTITLE)

    inp = sankey_data(flt, basis, focus_tool)
    graph = sankey.collapse_pathways(inp)
    if not graph.links:
        st.info(EMPTY_SANKEY_NOTE)
    else:
        fig = sankey.build_sankey(graph)
        st.plotly_chart(fig, config=theme.PLOTLY_CONFIG, width="stretch")
        volume_caption = _volume_caption(graph.merged_volume, graph.hidden_volume)
        if volume_caption:
            st.caption(volume_caption)

    st.caption(NOT_RECORDED_CAPTION)
    # A line that prints a bare dash tells a reader nothing; it is shown only when there is a
    # volume to disclose.
    if inp.excluded_display and inp.excluded_display != "—":
        st.caption(EXCLUDED_VOLUME_TEMPLATE.format(volume=inp.excluded_display))

    _render_mechanism_matrix(flt, basis)

    st.header("Mechanism trend")
    years = available_years()
    latest = years[-1] if years else "—"
    st.caption(trend.MECHANISM_TREND_PRE2019_NOTE.format(latest=latest))
    trend_df, gate_result = mechanism_trend_frame(flt)
    if gate_result.show_chart:
        st.caption(MECHANISM_TREND_SHOWN_CAPTION_TEMPLATE.format(latest=latest))
        trend_fig = trend.build_share_trend(trend_df)
        st.plotly_chart(trend_fig, config=theme.PLOTLY_CONFIG, width="stretch")
        st.caption(trend.DIRECTION_NOTE)
    else:
        gate = load_trend_gate()
        st.info(
            MECHANISM_TREND_INSUFFICIENT_TEMPLATE.format(min_years=gate.min_years, latest=latest)
        )

    maintenance_share = resolve_share(kpi_rows[_MAINTENANCE_ROW_INDEX]).display_value
    _render_setup_maintenance(flt, basis, maintenance_share)


render()
