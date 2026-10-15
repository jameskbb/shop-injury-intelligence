"""PAGE-01 Injury Overview (D-09). Section order per UI-SPEC: title/subtitle -> KPI strip ->
TOOL INJURY LANDSCAPE -> TOP TOOLS BY ESTIMATED ER BURDEN -> TOOL BURDEN BY YEAR -> INJURY TYPE
MIX -> WHAT THE DATA IS SAYING -> footer (rendered globally by app.py).

TOOL BURDEN BY YEAR (the tool x year heatmap) lives HERE and not on Tool Profiles because Tool
Profiles is scoped to exactly one tool (`filters.current_filter(tools_override=(tool_id,))`), so
the same chart there would be a single-row strip -- a worse rendering of the INJURY TREND line
chart that page already carries. Overview is the only page holding a workshop-wide, multi-tool
filter, and it already answers "which tools" (landscape, ranked bars); the heatmap slots directly
after the ranked bars to answer "and when", reusing the bars' own ranking order.
"""

from __future__ import annotations

import streamlit as st

from wii.analytics.insights import generate_insights, load_thresholds
from wii.app import filters, theme
from wii.app.charts.injury_mix import SUBTITLE as INJURY_MIX_SUBTITLE
from wii.app.charts.injury_mix import build_injury_mix
from wii.app.charts.landscape import SUBTITLE as LANDSCAPE_SUBTITLE
from wii.app.charts.landscape import build_landscape, suppressed_count
from wii.app.charts.ranked_bars import SUBTITLE as RANKED_BARS_SUBTITLE
from wii.app.charts.ranked_bars import (
    all_suppressed_note,
    build_ranked_bars,
    suppressed_footer_note,
)
from wii.app.charts.temporal_heatmap import (
    SCHEMA_CHANGE_CAPTION as TEMPORAL_SCHEMA_CHANGE_CAPTION,
)
from wii.app.charts.temporal_heatmap import SCHEMA_CHANGE_YEAR as TEMPORAL_SCHEMA_CHANGE_YEAR
from wii.app.charts.temporal_heatmap import SUBTITLE as TEMPORAL_HEATMAP_SUBTITLE
from wii.app.charts.temporal_heatmap import TITLE as TEMPORAL_HEATMAP_TITLE
from wii.app.charts.temporal_heatmap import YEAR_RANGE_NOTE as TEMPORAL_YEAR_RANGE_NOTE
from wii.app.charts.temporal_heatmap import (
    build_temporal_heatmap,
    limited_cell_count,
    suppressed_cell_count,
)
from wii.app.components.filter_context import render_filter_context, render_title_block
from wii.app.components.insight_card import render_insight_section
from wii.app.components.kpi_card import KpiCard, render_kpi_strip
from wii.app.components.stability_badge import (
    STATE_STABLE,
    basis_caption,
    empty_state,
    resolve,
    resolve_share,
    suppressed_share_row,
)
from wii.app.data import (
    burden_window_delta,
    cached_estimate_by,
    cached_estimate_total,
    default_overall_estimate,
    injury_mix_frame,
    insight_frames,
    landscape_frame,
    latest_data_year,
    overall_severity,
    share_window_delta,
    tool_year_matrix,
    tools_analyzed,
)
from wii.metrics import CaseFilter

TITLE = "What sends workshop users to the ER?"
SUBTITLE = (
    "National patterns in non-work-related consumer workshop injuries reported through CPSC NEISS."
)
DRAWING = "Workshop tool injury burden and severity"

# Metric definitions, surfaced on the card. Supplementary only -- the basis, the denominator and
# the stability label stay in each card's visible caption.
HAND_FINGER_TOOLTIP = (
    "Share of estimated ER-treated workshop injuries where a hand or finger is among the recorded "
    "body parts. A case can record several body parts, so these shares do not sum to 100%."
)
LACERATION_TOOLTIP = (
    "Share of estimated ER-treated workshop injuries with a laceration among ANY recorded "
    "diagnosis. The Injury type mix chart below splits cases by their single PRIMARY diagnosis, "
    "so its laceration bar is the smaller of the two figures."
)
ESCALATED_TOOLTIP = (
    "Share of estimated ER-treated workshop injuries whose ER visit ended in admission or "
    "transfer rather than release. Severity among ER cases, not a probability of injury."
)

# The two laceration figures on this page are computed on DIFFERENT bases -- any recorded
# diagnosis for the KPI, single primary diagnosis for the mix chart -- and shipped reading 57.6%
# and 51.0% within one screen of each other with nothing saying why. The basis is now stated on
# both, in the caption a reader meets before the number.
_LACERATION_KPI_LABEL = "Laceration share (any diagnosis)"
_INJURY_MIX_BASIS_NOTE = (
    "Each case is counted once, under its PRIMARY diagnosis only, so the laceration bar here is "
    "lower than the any-diagnosis laceration share in the strip above."
)


def _estimated_injuries_card(overall, flt: CaseFilter, basis: str) -> KpiCard:
    """The page's hero figure: the one number the landing page leads with, set at the measured-
    value size the rest of the strip is a step below. It used to share a size with "31 tools
    analyzed" and "2025", so the headline had no hierarchy over two pieces of metadata.
    """
    resolved = resolve(overall, basis)
    caption = basis_caption(basis, flt.years[0], flt.years[1]) if flt.years else ""
    if resolved.label:
        caption = f"{caption} ({resolved.label})" if caption else resolved.label
    # A comparison under a limited/suppressed headline number would be indefensible on its face,
    # so the delta is only offered when the card's own estimate is stable; `gated_delta` then
    # re-checks both windows independently.
    delta = burden_window_delta(flt) if resolved.state == STATE_STABLE else None
    return KpiCard(
        label="Estimated injuries",
        value=resolved.display_value,
        caption=caption,
        delta=delta,
        hero=True,
        dimension=f"{flt.years[0]}–{flt.years[1]}" if flt.years else "",
        tooltip=(
            "Survey-weighted national estimate of emergency-department-treated injuries "
            "associated with workshop tools, within the NEISS reporting population. NEISS has "
            "no count of tool owners or hours of use, so this is a burden, not a rate."
        ),
    )


def _share_caption(flt: CaseFilter, resolved) -> str:
    caption = (
        f"share of estimated U.S. ER-treated injuries, {flt.years[0]}–{flt.years[1]}"
        if flt.years
        else "share of estimated U.S. ER-treated injuries"
    )
    if resolved.label:
        caption = f"{caption} ({resolved.label})"
    return caption


def _share_card(
    label: str,
    profile,
    metric_id: str,
    flt: CaseFilter,
    *,
    severe: bool = False,
    tooltip: str = "",
) -> KpiCard:
    rows = profile.filter(profile["metric_id"] == metric_id)
    if rows.height == 0:
        # No row for this metric_id in the current domain (e.g. zero-case group) -- treat as hard
        # suppressed, never as a zero share.
        row = suppressed_share_row()
    else:
        row = rows.row(0, named=True)
    resolved = resolve_share(row)
    delta = share_window_delta(flt, metric_id) if resolved.state == STATE_STABLE else None
    return KpiCard(
        label=label,
        value=resolved.display_value,
        caption=_share_caption(flt, resolved),
        severe=severe,
        delta=delta,
        tooltip=tooltip,
    )


def render() -> None:
    st.title(TITLE)
    st.caption(SUBTITLE)

    flt = filters.current_filter()
    basis = st.session_state.get("basis", "annual_average")
    if flt == filters.default_case_filter():
        overall = default_overall_estimate(flt.age_scope)
    else:
        overall = cached_estimate_total(flt)

    if overall is None or overall.raw_n == 0:
        # The title block still renders above this, so a reader looking at an empty page can
        # see which scope emptied it.
        render_title_block(DRAWING)
        empty_state(filters.filter_summary())
        return

    # "Tools analyzed" and "Latest data year" used to occupy two of six KPI slots. Neither is an
    # estimate: one is a count of sampling coverage with no interval to compare against, the
    # other is dataset metadata already in the footer and in the year chip. Both are scope facts,
    # so both now live in the title block, which is what a title block is for.
    render_title_block(
        DRAWING,
        scope_extra=f"{tools_analyzed(flt):,} tools with reportable data",
        latest_year=latest_data_year(),
    )
    # Only when something is actually filtered: the title block directly above already states the
    # basis, the scope and the source, so on a default view the chip row restated all three.
    render_filter_context(only_when_active=True)

    severity = overall_severity(flt)
    render_kpi_strip(
        [
            _estimated_injuries_card(overall, flt, basis),
            _share_card(
                "Hand/finger share",
                severity,
                "hand_finger",
                flt,
                tooltip=HAND_FINGER_TOOLTIP,
            ),
            _share_card(
                _LACERATION_KPI_LABEL, severity, "laceration", flt, tooltip=LACERATION_TOOLTIP
            ),
            _share_card(
                "Escalated-care share",
                severity,
                "escalated_care",
                flt,
                severe=True,
                tooltip=ESCALATED_TOOLTIP,
            ),
        ]
    )

    _render_landscape(flt, basis)
    _render_ranked_bars(flt, basis)
    _render_temporal_heatmap(flt)
    _render_injury_mix(flt)
    _render_insight_section(flt, basis)


def _render_landscape(flt: CaseFilter, basis: str) -> None:
    st.header("Tool injury landscape")
    excluded = suppressed_count(landscape_frame(flt, basis))
    caption = LANDSCAPE_SUBTITLE
    if excluded:
        caption = f"{caption} {excluded} tool(s) with a suppressed estimate are excluded."
    st.caption(caption)
    fig = build_landscape(landscape_frame(flt, basis), basis)
    st.plotly_chart(fig, config=theme.PLOTLY_CONFIG, width="stretch")


def _render_ranked_bars(flt: CaseFilter, basis: str) -> None:
    """Suppressed tools come OFF the axis and into a footer line: kept as ranks they printed
    nothing but an em dash for 14 of 30 rows, so half the chart's height was blank and the reader
    scrolled past it to reach the next section. `suppressed_footer_note` still counts and names
    every one of them -- which tools have no reportable estimate is itself a finding about this
    dataset, so it is disclosed, just not at the cost of half a chart.

    The builder returns `None` when NOTHING is drawable. A narrow filter can empty this frame, and
    an all-suppressed ranked chart autoranges to a zero-width axis whose `~s` ticks read
    "-500m ... 500m" beside a column of dashes. One honest line is printed instead.
    """
    st.header("Top tools by estimated ER burden")
    st.caption(RANKED_BARS_SUBTITLE)
    value_label = (
        "Estimated annual average" if basis == "annual_average" else "Estimated period total"
    )
    rows = cached_estimate_by(flt, "tool")
    fig = build_ranked_bars(rows, basis, value_label, suppressed_footer=True)
    if fig is None:
        st.info(all_suppressed_note(rows, basis, "tools"))
        return
    st.plotly_chart(fig, config=theme.PLOTLY_CONFIG, width="stretch")
    footer = suppressed_footer_note(rows, basis)
    if footer:
        st.caption(footer)


def _render_temporal_heatmap(flt: CaseFilter) -> None:
    """The heatmap takes no `basis` argument on purpose -- a cell is one year, so its only honest
    basis is that year's own period total (see `charts.temporal_heatmap`). The counts of unfilled
    and flagged cells are stated in the caption rather than left for the reader to tally off the
    grid, matching the landscape's own "N tool(s) ... excluded" convention.
    """
    st.header(TEMPORAL_HEATMAP_TITLE)
    df, omitted_tools = tool_year_matrix(flt)
    caption_parts = [TEMPORAL_HEATMAP_SUBTITLE, TEMPORAL_YEAR_RANGE_NOTE]
    suppressed = suppressed_cell_count(df)
    limited = limited_cell_count(df)
    if suppressed or limited:
        caption_parts.append(
            f"{suppressed} cell(s) have no reportable estimate and {limited} are "
            "limited-sample in the current filter."
        )
    if omitted_tools:
        caption_parts.append(
            f"{omitted_tools} lower-burden tool(s) are not shown; the full ranking is on the "
            "Tool Comparison and Tool Profiles pages."
        )
    st.caption(" ".join(caption_parts))
    st.plotly_chart(build_temporal_heatmap(df), config=theme.PLOTLY_CONFIG, width="stretch")
    if df.height:
        years = df["year"].to_list()
        if min(years) < TEMPORAL_SCHEMA_CHANGE_YEAR <= max(years):
            st.caption(TEMPORAL_SCHEMA_CHANGE_CAPTION)


def _render_injury_mix(flt: CaseFilter) -> None:
    st.header("Injury type mix")
    st.caption(f"{INJURY_MIX_SUBTITLE} {_INJURY_MIX_BASIS_NOTE}")
    fig = build_injury_mix(injury_mix_frame(flt))
    st.plotly_chart(fig, config=theme.PLOTLY_CONFIG, width="stretch")


def _render_insight_section(flt: CaseFilter, basis: str) -> None:
    st.header("What the data is saying")
    st.caption(
        "Deterministic patterns that clear stability and effect-size thresholds in the current "
        "filter selection, never padded with weaker statements."
    )
    thresholds = load_thresholds()
    frames = insight_frames(flt, basis)
    insights = generate_insights(frames, thresholds, basis)
    render_insight_section(insights, thresholds.min_cards_for_section)


render()
