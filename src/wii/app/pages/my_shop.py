"""PAGE-06 "What should I be mindful of in my shop?" (D-01, D-02, D-06). This page owns the
`my_shop_selected_tools` session-state key (+ its `widget_my_shop_selected_tools` twin, Pitfall
4) -- distinct from the global sidebar's `global_tool_filter`, which `app.py` hides on this page
via `_HIDE_TOOL_PAGES`. Unlike Tool Profiles, the selection starts EMPTY: there is NO
`_ensure_default_tool`-style auto-selection here, because D-01 forbids presenting a preselected
"typical shop." Section order (05-UI-SPEC.md "Per-Page Layout Contracts -> My Shop"): multiselect
-> title -> one-line disclaimer -> title block -> empty state OR the injury profile -> the
full disclaimer directly above the footer. Exactly one call to `render_disclaimer_line` and one
to `render_disclaimer` exist in this module (never one per branch), so every render path -- empty
selection, zero-case selection, populated -- carries the caveat both before and after the
numbers.
"""

from __future__ import annotations

import html
from dataclasses import replace

import polars as pl
import streamlit as st

from wii.analytics.insights import load_thresholds
from wii.app import filters, theme
from wii.app.charts import profile_bars
from wii.app.components.body_map import (
    VIEWS_FRONT,
    BodyScale,
    RegionValue,
    render_body_map,
)
from wii.app.components.disclaimer_banner import render_disclaimer, render_disclaimer_line
from wii.app.components.filter_context import render_title_block
from wii.app.components.kpi_card import KpiCard, render_kpi_strip
from wii.app.components.narrative_card import narrative_list_html
from wii.app.components.stability_badge import (
    SUPPRESSED_LABEL,
    basis_caption,
    empty_state,
    format_share,
    order_resolved,
    resolve,
    resolve_share,
    suppressed_share_row,
)
from wii.app.components.watchout_card import watchout_grid_html
from wii.app.data import (
    RankedShares,
    anatomy_snapshot_frame,
    cached_estimate_total,
    cached_share_by,
    filtered_narratives,
    injury_mix_frame,
    load_dims,
    maintenance_context_frame,
    mechanism_profile_frame,
    selected_tool_share_ranking,
    watchout_frames,
)
from wii.metrics import CaseFilter

PAGE_TITLE = "What should I be mindful of in my shop?"
SELECT_LABEL = "Select the tools you regularly use"
EMPTY_HEADING = "Select your tools to see your shop's injury profile."
EMPTY_BODY_TEMPLATE = (
    "Choose one or more tools above -- from the {n} tracked workshop tools -- to see an "
    "aggregate injury profile, watch-out cards, and a body map scoped to your selection."
)
PROFILE_HEADING = "My shop injury profile"
_SHARE_OF_WORKSHOP_CAPTION = (
    "share of all workshop ER injuries -- a case involving several of your tools counts once"
)
_TOP_REGIONS_HEADING = "Top body regions (any recorded)"
_TOP_REGIONS_CAPTION = (
    "Share of your selection's ER cases that record each region -- a case can record several, "
    "so shares can sum past 100%."
)
_TOP_REGIONS_LIMIT = 5
_TOP_DIAGNOSES_HEADING = "Top diagnosis categories"
_TOP_MECHANISMS_HEADING = "Top primary mechanisms"
_TOP_MECHANISMS_CAPTION = (
    "Narrative-derived: classified from ER narrative text, not a coded NEISS field; unknown and "
    "other are excluded."
)
_MECHANISM_EXCLUDED = frozenset({"unknown", "other"})
_OUTCOME_COLUMNS: tuple[tuple[str, str], ...] = (
    ("escalated_care", "Escalated care"),
    ("amputation", "Amputation"),
    ("eye", "Eye"),
)
_OUTCOME_TABLE_HEADING = "Outcome mix across your tools"
_OUTCOME_TABLE_CAPTION = (
    "Share of each tool's own ER cases recording that outcome: a within-tool share, so the "
    "columns describe what those ER visits looked like, not how likely an injury is. A dashed "
    "value is a limited sample; an em dash means the estimate is below the reporting floor."
)
_ESTIMATE_TOOLTIP = (
    "Survey-weighted national estimate of ER-treated injuries associated with the tools you "
    "selected, within the NEISS reporting population. A case involving several of your tools is "
    "counted once, so this is not the sum of the per-tool figures."
)
_SHARE_TOOLTIP = (
    "The same estimate as a share of all estimated workshop ER injuries under the current "
    "filters. It describes how much of the workshop total your tools account for, not how "
    "likely an injury is when you use them."
)
DISCLAIMER_TOP_LINE = (
    "These patterns describe ER-treated injuries associated with these tools; they are not your "
    "personal probability of injury. Full note at the foot of the page."
)
_LIMITED_DIVIDER_HTML = (
    '<div class="wii-limited-divider">Limited sample -- interpret cautiously</div>'
)
_NO_STABLE_DATA_NOTE = "No selected tool has enough cases for this measure."
PROTECT_HEADING = "Protect this"
PROTECT_SUBTITLE = (
    "Body regions scaled to your selected tools only -- the strongest hotspots stand out first."
)
PROTECT_ALL_SUPPRESSED_NOTE = (
    "No body region clears the reporting floor for this selection, so the figure above carries "
    "no shading at all. Hover any region for its own sample count and stability."
)
WATCHOUTS_HEADING = "Watch-outs"
WATCHOUTS_CAPTION = (
    "One card per selected tool, ordered by estimated burden. Every value is computed from the "
    "data under the current filters."
)
WATCHOUT_GRID_LIMIT = 6
NARRATIVES_HEADING = "What the narratives repeat"
NARRATIVES_GUARDRAIL = (
    "These are descriptive patterns in how incidents are reported, not a proven prevention method."
)
MAINTENANCE_NONE_COPY = (
    "No maintenance or setup context clears the stability threshold for this selection."
)
NARRATIVES_PER_MECHANISM = 2
_UNKNOWN_MECHANISM_SHARE_TEMPLATE = (
    "{share} of your selection's narratives have no classifiable mechanism."
)
_MAINTENANCE_HEADING = "Setup and maintenance context"
_MAINTENANCE_CAPTION = (
    "Narrative-derived: classified from ER narrative text, not a coded NEISS field."
)
_REPRESENTATIVE_HEADING = "Representative narratives"
_TOP_MECHANISM_COUNT = 3


def _tool_options_and_labels() -> tuple[list[str], dict[str, str]]:
    """Page-local copy of `tool_profiles._tool_options_and_labels`' selector-label shape
    (narrative-refined tools suffixed) -- My Shop never imports another page module."""
    tools = load_dims()["tools"]
    options = tools["tool_id"].to_list()
    selector_labels: dict[str, str] = {}
    for row in tools.iter_rows(named=True):
        selector_labels[row["tool_id"]] = (
            f"{row['label']} (narrative-refined)"
            if row["source"] == "narrative_refined"
            else row["label"]
        )
    return options, selector_labels


def _sync_selected_tools() -> None:
    st.session_state["my_shop_selected_tools"] = tuple(
        st.session_state["widget_my_shop_selected_tools"]
    )


def _render_multiselect(options: list[str], labels: dict[str, str]) -> None:
    st.session_state.setdefault("my_shop_selected_tools", ())
    valid = tuple(t for t in st.session_state["my_shop_selected_tools"] if t in options)
    if valid != st.session_state["my_shop_selected_tools"]:
        st.session_state["my_shop_selected_tools"] = valid
    if "widget_my_shop_selected_tools" not in st.session_state:
        st.session_state["widget_my_shop_selected_tools"] = list(valid)
    st.multiselect(
        SELECT_LABEL,
        options=options,
        format_func=lambda tid: labels.get(tid, tid),
        key="widget_my_shop_selected_tools",
        on_change=_sync_selected_tools,
    )


def _estimated_injuries_card(sel_flt: CaseFilter, basis: str) -> KpiCard:
    overall = cached_estimate_total(sel_flt)
    resolved = resolve(overall, basis)
    caption = basis_caption(basis, sel_flt.years[0], sel_flt.years[1]) if sel_flt.years else ""
    if resolved.label:
        caption = f"{caption} ({resolved.label})" if caption else resolved.label
    return KpiCard(
        label="Estimated injuries",
        value=resolved.display_value,
        caption=caption,
        hero=True,
        dimension=f"{sel_flt.years[0]}–{sel_flt.years[1]}" if sel_flt.years else "",
        tooltip=_ESTIMATE_TOOLTIP,
    )


def _share_of_workshop_card(workshop_flt: CaseFilter, sel_flt: CaseFilter) -> KpiCard:
    share_df = cached_share_by(workshop_flt, sel_flt, by="all")
    row = share_df.row(0, named=True) if share_df.height else suppressed_share_row()
    resolved = resolve_share(row)
    caption = _SHARE_OF_WORKSHOP_CAPTION
    if resolved.label:
        caption = f"{caption} ({resolved.label})"
    return KpiCard(
        label="Share of workshop burden",
        value=resolved.display_value,
        caption=caption,
        tooltip=_SHARE_TOOLTIP,
    )


def _share_rows_html(stable: list[dict], limited: list[dict], suppressed_count: int) -> str:
    parts: list[str] = []
    for entry in stable:
        label = html.escape(entry["label"])
        value = html.escape(entry["display_value"])
        parts.append(f'<div class="wii-shop-list-row">{label} · {value}</div>')
    if limited:
        parts.append(_LIMITED_DIVIDER_HTML)
        for entry in limited:
            label = html.escape(entry["label"])
            value = html.escape(entry["display_value"])
            parts.append(f'<div class="wii-shop-list-row">{label} · {value}</div>')
    if suppressed_count > 0:
        note = html.escape(f"{suppressed_count} selected tool(s) omitted -- {SUPPRESSED_LABEL}")
        parts.append(f'<div class="wii-limited-divider">{note}</div>')
    if not parts:
        parts.append(f'<div class="wii-shop-list-row">{html.escape(_NO_STABLE_DATA_NOTE)}</div>')
    return f'<div class="wii-shop-list">{"".join(parts)}</div>'


def _render_top_regions(sel_flt: CaseFilter, basis: str) -> None:
    st.subheader(_TOP_REGIONS_HEADING)
    st.caption(_TOP_REGIONS_CAPTION)
    values = anatomy_snapshot_frame(sel_flt, basis)
    dims = load_dims()["body_regions_all"]
    region_ids = {row["region_id"] for row in dims.iter_rows(named=True) if row["kind"] == "region"}
    rows = [
        {"label": rv.label, "value": rv.value, "display_value": rv.display_value, "state": rv.state}
        for region_id, rv in values.items()
        if region_id in region_ids
    ]
    ordered = order_resolved(rows)
    stable = [r for r in ordered if r["state"] == "stable"][:_TOP_REGIONS_LIMIT]
    limited = [r for r in ordered if r["state"] == "limited"]
    st.markdown(_share_rows_html(stable, limited, 0), unsafe_allow_html=True)


def _top_mechanisms_frame(sel_flt: CaseFilter, baseline_flt: CaseFilter | None = None):
    """`mechanism_profile_frame(sel_flt)` with `unknown`/`other` removed (narrative-derived;
    unknown and other are excluded and the caption says so) -- a page-local helper so the
    exclusion rule is unit-testable without parsing the rendered Plotly figure. `baseline_flt`
    threads the all-workshop comparator through to the bars' ghost ticks."""
    mech_df = mechanism_profile_frame(sel_flt, baseline_flt)
    if mech_df.height == 0:
        return mech_df
    return mech_df.filter(~mech_df["category"].is_in(list(_MECHANISM_EXCLUDED)))


def outcome_table_html(columns: list[tuple[str, RankedShares]], tool_labels: list[str]) -> str:
    """One row per selected tool, one column per outcome -- the three ranked text lists folded
    into a single small table.

    As three separate lists the stability channel was a divider line between blocks, which
    ordering alone cannot carry: a limited "Sander 16.3%" sat above a stable "Bench or Table Saw
    0.9%" under "Tools most associated with eye injuries", and "Router 0.0%" was listed as
    "associated with" an outcome it had no reportable estimate for. In a table each CELL carries
    its own state -- a dashed underline for limited, an em dash for suppressed -- so a reader
    compares like with like across a row and down a column at once.
    """
    heads = "".join(f"<th>{html.escape(name)}</th>" for name, _ in columns)
    by_tool: dict[str, dict[str, tuple[str, str]]] = {}
    for name, ranked in columns:
        for state, entries in (("stable", ranked.stable), ("limited", ranked.limited)):
            for entry in entries:
                by_tool.setdefault(entry["label"], {})[name] = (entry["display_value"], state)
    rows: list[str] = []
    for label in tool_labels:
        cells: list[str] = []
        for name, _ranked in columns:
            value, state = by_tool.get(label, {}).get(name, ("Not reportable", "suppressed"))
            css = "wii-limited" if state == "limited" else ""
            css = "wii-comparison-suppressed" if state == "suppressed" else css
            span = f'<span class="{css}">{html.escape(value)}</span>' if css else html.escape(value)
            cells.append(f"<td>{span}</td>")
        rows.append(f"<tr><td>{html.escape(label)}</td>{''.join(cells)}</tr>")
    return (
        '<table class="wii-comparison-strip"><thead><tr><th>Your tool</th>'
        f"{heads}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _render_outcome_table(sel_flt: CaseFilter, tool_labels: list[str]) -> None:
    st.subheader(_OUTCOME_TABLE_HEADING)
    st.caption(_OUTCOME_TABLE_CAPTION)
    columns = [
        (short, selected_tool_share_ranking(sel_flt, metric_id))
        for metric_id, short in _OUTCOME_COLUMNS
    ]
    st.markdown(outcome_table_html(columns, tool_labels), unsafe_allow_html=True)


def _protect_scale(values: dict[str, RegionValue], region_ids: set[str]) -> BodyScale:
    """Page-local copy of `tool_profiles._anatomy_scale` (D-04: this page's selection is its own
    one-render scale, never shared with another tool's scale)."""
    candidates = [
        rv.value
        for region_id, rv in values.items()
        if region_id in region_ids and rv.value is not None
    ]
    vmax = max(candidates) if candidates else 0.0
    return BodyScale(vmin=0.0, vmax=vmax if vmax > 0 else 1.0)


def _render_protect_this(sel_flt: CaseFilter, basis: str) -> None:
    """Front plate only, with the key and the stability-floor count under it.

    Under a heading that says "Protect this", the figure used to be hatched crown to toe except
    the hands: it read "protect everything", and the hands -- the actual answer -- were the
    quietest marks on it. Replacing the hatch with a dashed outline did not fix that on its own,
    because 20 of 22 regions fall below the floor here, so the dashes simply became the new
    loudest thing. Unstable regions now recede to bare sheet on one quiet hairline and the hands
    carry the only ink.

    The top-region chips that used to sit under the figure are gone: they repeated "Top body
    regions" from 400px above, the third rendering of finger/hand on one page.
    """
    st.header(PROTECT_HEADING)
    st.caption(PROTECT_SUBTITLE)
    values = anatomy_snapshot_frame(sel_flt, basis)
    dims = load_dims()["body_regions_all"]
    region_ids = {row["region_id"] for row in dims.iter_rows(named=True) if row["kind"] == "region"}
    scale = _protect_scale(values, region_ids)
    # `confidence_outlines=False`: per-region dashes are for the full-size Body Map figure alone.
    # On a typical shop 20 of 22 regions fall below the floor, so the dashes outnumber the inked
    # regions about 10 to 1 and stop being a line treatment -- under a heading that says "Protect
    # this" the plate read as a dashed skeleton with two blue hands. The floor count carries the
    # fact in words, and every region keeps its own `<title>` stability.
    render_body_map(
        values,
        scale,
        instance_id="my-shop",
        views=VIEWS_FRONT,
        region_ids=region_ids,
        confidence_outlines=False,
    )
    stable_regions = [
        rv for rid, rv in values.items() if rid in region_ids and rv.state == "stable"
    ]
    if not stable_regions:
        st.caption(PROTECT_ALL_SUPPRESSED_NOTE)


def _render_watchouts(workshop_flt: CaseFilter, basis: str, tool_ids: tuple[str, ...]) -> None:
    """WATCH-OUTS (D-03): first `WATCHOUT_GRID_LIMIT` cards in one grid; the rest (if any) inside
    an `st.expander` labelled "Show {N} more tools"."""
    st.header(WATCHOUTS_HEADING)
    st.caption(WATCHOUTS_CAPTION)
    watchouts = watchout_frames(workshop_flt, basis, tool_ids)
    if not watchouts:
        return
    first, rest = watchouts[:WATCHOUT_GRID_LIMIT], watchouts[WATCHOUT_GRID_LIMIT:]
    st.markdown(watchout_grid_html(first), unsafe_allow_html=True)
    if rest:
        with st.expander(f"Show {len(rest)} more tools"):
            st.markdown(watchout_grid_html(rest), unsafe_allow_html=True)


def _ranked_mechanism_rows(mech_df: pl.DataFrame) -> list[dict]:
    """Every non-`unknown`/`other` row of `mech_df` (already `stability_badge`-resolved by
    `mechanism_profile_frame`), stable-first-desc-by-value then limited-desc-by-value then
    suppressed (D-07, via `order_resolved` -- no re-resolution needed)."""
    if mech_df.height == 0:
        return []
    rows = [
        dict(r) for r in mech_df.iter_rows(named=True) if r["category"] not in _MECHANISM_EXCLUDED
    ]
    return order_resolved(rows)


def _partition_maintenance_rows(
    context_df: pl.DataFrame, basis: str
) -> tuple[list[dict], list[dict]]:
    """Pure partition helper for "Setup and maintenance context" (D-05): `maintenance_context_frame`
    returns raw `EstimateRecord`-shaped rows (already stable-first ordered), which this resolves
    via `basis` into (stable, limited) display-ready row lists -- suppressed rows are dropped."""
    stable: list[dict] = []
    limited: list[dict] = []
    for row in context_df.iter_rows(named=True):
        resolved = resolve(row, basis)
        entry = {"label": row["display_label"], "display_value": resolved.display_value}
        if resolved.state == "stable":
            stable.append(entry)
        elif resolved.state == "limited":
            limited.append(entry)
    return stable, limited


def _render_maintenance_context(sel_flt: CaseFilter, basis: str) -> None:
    """ "Setup and maintenance context" (D-05): stable/limited rows only, `MAINTENANCE_NONE_COPY`
    when neither exists."""
    st.subheader(_MAINTENANCE_HEADING)
    context_df = maintenance_context_frame(sel_flt, basis)
    stable, limited = _partition_maintenance_rows(context_df, basis)
    if not stable and not limited:
        # A muted caption, not `st.info`: a blue box reads as an alert, and "nothing here clears
        # the floor" is an ordinary, expected outcome for a 0.3%-of-cases mechanism.
        st.caption(MAINTENANCE_NONE_COPY)
        return
    st.caption(_MAINTENANCE_CAPTION)
    st.markdown(_share_rows_html(stable, limited, 0), unsafe_allow_html=True)


def _render_representative_narratives(sel_flt: CaseFilter, top_mechanisms: tuple[str, ...]) -> None:
    """ "Representative narratives" (D-05): up to `NARRATIVES_PER_MECHANISM` cards per top-3 STABLE
    mechanism, via `filtered_narratives(replace(sel_flt, mechanisms=(m,)), narrative_min_confidence,
    limit=NARRATIVES_PER_MECHANISM)` -- no placeholder slots for a mechanism with fewer than 2."""
    st.subheader(_REPRESENTATIVE_HEADING)
    if not top_mechanisms:
        return
    thresholds = load_thresholds()
    for mechanism in top_mechanisms:
        mech_flt = replace(sel_flt, mechanisms=(mechanism,))
        narratives = filtered_narratives(
            mech_flt, thresholds.narrative_min_confidence, limit=NARRATIVES_PER_MECHANISM
        )
        st.subheader(theme.mechanism_label(mechanism))
        if narratives.height == 0:
            continue
        st.markdown(narrative_list_html(narratives.iter_rows(named=True)), unsafe_allow_html=True)


def _top_stable_mechanisms(sel_flt: CaseFilter) -> tuple[str, ...]:
    """The top `_TOP_MECHANISM_COUNT` STABLE mechanism ids, and the unknown-mechanism share line.

    This replaces "Mechanism shares across your tools", which drew the SAME frame as the "Top
    primary mechanisms" bars 400px above it, as a text list. The unknown-share caption that hung
    under it is kept and moves up beside the bars -- it is the one thing that section said that
    the chart does not, and dropping it would hide how much of the selection has no classifiable
    mechanism at all.
    """
    mech_df = mechanism_profile_frame(sel_flt)
    ranked = _ranked_mechanism_rows(mech_df)
    stable = [r for r in ranked if r["state"] == "stable"]
    if mech_df.height:
        unknown_rows = mech_df.filter(mech_df["category"] == "unknown")
        if unknown_rows.height:
            unknown_row = unknown_rows.row(0, named=True)
            if unknown_row["state"] != "suppressed":
                st.caption(
                    _UNKNOWN_MECHANISM_SHARE_TEMPLATE.format(
                        share=format_share(unknown_row["value"])
                    )
                )
    return tuple(r["category"] for r in stable[:_TOP_MECHANISM_COUNT])


def _render_narratives(sel_flt: CaseFilter, basis: str, top_mechanisms: tuple[str, ...]) -> None:
    st.header(NARRATIVES_HEADING)
    st.caption(NARRATIVES_GUARDRAIL)
    _render_maintenance_context(sel_flt, basis)
    _render_representative_narratives(sel_flt, top_mechanisms)


def render() -> None:
    options, selector_labels = _tool_options_and_labels()
    _render_multiselect(options, selector_labels)
    st.title(PAGE_TITLE)
    # One line at the top, the full text above the footer. The 40-word block appeared twice, and
    # the first copy pushed the KPI strip below the fold on a phone -- the reader met the caveat
    # before meeting anything it was a caveat about. The full statement is still rendered on
    # every path, including the empty-selection and zero-case branches.
    render_disclaimer_line(DISCLAIMER_TOP_LINE)

    selected = st.session_state["my_shop_selected_tools"]
    if not selected:
        st.subheader(EMPTY_HEADING)
        tool_count = load_dims()["tools"].height
        st.markdown(EMPTY_BODY_TEMPLATE.format(n=tool_count))
    else:
        basis = st.session_state.get("basis", "annual_average")
        sel_flt = filters.current_filter(tools_override=selected)
        workshop_flt = filters.current_filter(tools_override=())
        render_title_block(
            "Injury profile for the tools in your shop",
            tools_override=selected,
        )
        overall = cached_estimate_total(sel_flt)
        if overall is None or overall.raw_n == 0:
            empty_state(filters.filter_summary())
        else:
            st.header(PROFILE_HEADING)
            render_kpi_strip(
                [
                    _estimated_injuries_card(sel_flt, basis),
                    _share_of_workshop_card(workshop_flt, sel_flt),
                ]
            )
            _render_top_regions(sel_flt, basis)
            st.subheader(_TOP_DIAGNOSES_HEADING)
            st.plotly_chart(
                profile_bars.build_diagnosis_bars(injury_mix_frame(sel_flt, workshop_flt)),
                config=theme.PLOTLY_CONFIG,
                width="stretch",
            )
            st.subheader(_TOP_MECHANISMS_HEADING)
            st.caption(_TOP_MECHANISMS_CAPTION)
            st.plotly_chart(
                profile_bars.build_mechanism_bars(_top_mechanisms_frame(sel_flt, workshop_flt)),
                config=theme.PLOTLY_CONFIG,
                width="stretch",
            )
            top_mechanisms = _top_stable_mechanisms(sel_flt)
            _render_outcome_table(sel_flt, [selector_labels.get(t, t) for t in selected])
            _render_protect_this(sel_flt, basis)
            _render_watchouts(workshop_flt, basis, selected)
            _render_narratives(sel_flt, basis, top_mechanisms)

    render_disclaimer()


render()
