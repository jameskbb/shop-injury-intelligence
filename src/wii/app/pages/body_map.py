"""PAGE-03 Body Map (D-01..D-07): interactive front/back SVG silhouette with page-level filters,
click-to-filter via `region_select` (mode fixed by the 04-01 Wave-0 spike verdict -- pills,
primary), rich D-04 tooltips, and a REGION DETAIL section scoped to the selected region (or all
regions when none is selected). This page owns its own tool selector under `body_map_tool` --
distinct from the global sidebar's `global_tool_filter` key, which app.py hides here (D-01, same
pattern as Tool Profiles' D-03). Task 3 adds the BODY PART x TOOL MATRIX.
"""

from __future__ import annotations

import html
from dataclasses import replace

import polars as pl
import streamlit as st

from wii.analytics.insights import load_thresholds
from wii.app import filters, theme
from wii.app.charts import profile_bars, ranked_bars, region_tool_matrix
from wii.app.components import region_select, stability_badge
from wii.app.components.body_map import (
    BodyScale,
    build_callout_chips_html,
    render_body_map,
    render_clickable_body_view,
)
from wii.app.components.filter_context import render_title_block
from wii.app.components.stability_badge import basis_caption, empty_state
from wii.app.data import (
    body_map_frame,
    cached_estimate_by,
    cached_estimate_total,
    filtered_narratives,
    injury_mix_frame,
    latest_data_year,
    load_dims,
    mechanism_options,
    mechanism_profile_frame,
    region_share_by_tool_frame,
    region_top_tools,
)
from wii.metrics import CaseFilter

PAGE_TITLE = "Where workshop injuries happen"
#: The title block's DRAWING field: what this sheet draws, in one short line. Every analytical
#: page opens with the strip -- it is the device the layout is named for, and it carries the
#: source/freshness line, which is read from the data rather than written here.
DRAWING = "Where workshop injuries occur on the body"
# The title block's DRAWING field now says what the sheet draws, so the subtitle no longer
# repeats it. What it says instead is the one thing nothing else above the fold does: how to
# operate the plate.
PAGE_SUBTITLE = (
    "Pick a tool to see where its injuries concentrate, then select a region for the cases "
    "behind it."
)

MEASURES: dict[str, str] = {
    "estimated": "Estimated injuries",
    "share": "Share of selected tool injuries",
    "amputation": "Amputations",
    "laceration": "Lacerations",
    "fracture": "Fractures",
    "escalated": "Escalated-care injuries",
}
_MEASURE_LABEL_TO_ID: dict[str, str] = {v: k for k, v in MEASURES.items()}

BASIS_CAPTION_ANY = (
    "Region values use the any-recorded body basis: a case may involve more than one region, "
    "so region shares do not sum to 100%."
)
ALL_SUPPRESSED_CAPTION = (
    "Every region is suppressed for this filter selection: the selection is too narrow for "
    "stable estimates. Try widening the year range or filters."
)
MEASURE_OVERRIDE_CAPTION = (
    "This measure's own diagnosis category is shown for the silhouette instead of the page "
    "diagnosis-category selector above."
)
SHARE_ALL_TOOLS_CAPTION = (
    "No tool selected: for All workshop tools, this shows each region's share of all "
    "workshop injuries, not a within-tool share."
)
MECHANISM_PROVENANCE_CAPTION = (
    "Mechanism categories are narrative-derived (mined from ER visit narratives), not a coded "
    "NEISS field."
)
NO_REGION_COPY = (
    "Showing all regions. Select a body region above to see its diagnosis mix, mechanism mix, "
    "top tools, and representative narratives."
)
ZERO_NARRATIVES_COPY = (
    "No high-confidence narrative examples available for this region under the current filters."
)
MATRIX_CAPTION = (
    "Within-tool share of injuries involving each region (any recorded body part, so rows can "
    "sum to more than 100%)."
)
MATRIX_CALLOUT_NOTE = (
    "Callout categories (internal, 25-50% of body, all parts of body, not recorded) are "
    "excluded from this matrix."
)
MATRIX_TOP_N = 15
# The 3D figure encodes the measure twice -- ink density and relief -- so the second channel is
# explained in words, next to the figure, like every other encoding on this page. The relief is
# real: `body3d.js` displaces a stable region along its own normals, at most 0.03 world units,
# scaled by the region's value and faded to nothing at the seam.
DEPTH_CAPTION = (
    "Ink density and relief both show the selected measure: the more injuries associated with a "
    "region, the darker its fill and the further it stands proud of the surface. A dashed "
    "outline marks a limited-sample or suppressed estimate; a suppressed region is left "
    "unfilled, and a region with no recorded cases keeps only the faintest outline."
)
#: Row 7: the key is page HTML under the figure on EVERY viewport -- never inside the canvas,
#: never hidden by a media query. Colours come from `theme`; nothing is hard-coded here.
#: It names only the EXCEPTIONS: a stable region carries no outline at all, because its fill is
#: what says "region" -- outlining all 22 turned the figure into a paint-by-numbers grid and made
#: the construction line, not the data, the loudest thing on the plate.
_LEGEND_STOPS = theme.SEQUENTIAL_SCALE[1:]
BODY3D_LEGEND_HTML = (
    '<div class="wii-body-key">'
    '<span class="wii-body-key-item"><span class="wii-body-key-label">Fewer</span>'
    + "".join(
        f'<span class="wii-body-key-swatch" style="background:{c}"></span>' for c in _LEGEND_STOPS
    )
    + '<span class="wii-body-key-label">more estimated injuries</span></span>'
    '<span class="wii-body-key-item">'
    f'<span class="wii-body-key-line" style="border-top:1px dashed {theme.CONSTRUCTION}"></span>'
    '<span class="wii-body-key-label">Limited sample</span></span>'
    '<span class="wii-body-key-item">'
    f'<span class="wii-body-key-line" style="border-top:1px dashed {theme.CONSTRUCTION}"></span>'
    '<span class="wii-body-key-label">Suppressed &mdash; unfilled</span></span>'
    '<span class="wii-body-key-item">'
    f'<span class="wii-body-key-line" style="border-top:1px solid {theme.RULE}"></span>'
    '<span class="wii-body-key-label">No recorded cases</span></span>'
    "</div>"
    "<style>"
    ".wii-body-key{display:flex;flex-wrap:wrap;gap:6px 22px;margin:6px 0 2px;"
    f"font-size:{theme.TYPE_MICRO}px;color:{theme.GRAPHITE_SOFT};" + "}"
    ".wii-body-key-item{display:inline-flex;align-items:center;gap:6px;}"
    ".wii-body-key-swatch{width:20px;height:9px;display:inline-block;}"
    ".wii-body-key-line{width:26px;height:0;display:inline-block;}"
    "</style>"
)
#: BODY-12: in-cell text is unreadable below this cell width, so it is dropped and the hover
#: tooltip carries the number instead. 22 region columns only clear it on a wide viewport, and
#: the page cannot measure one -- so the matrix prints numbers only when the tool axis is short
#: enough that the plot is not already dense.
MATRIX_MIN_TEXT_CELL_PX = region_tool_matrix.MIN_TEXT_CELL_PX
MATRIX_TEXT_MAX_TOOLS = 8
FINGER_CAPTION = (
    "NEISS records fingers as a single body-part category, so the map lights every finger "
    "together; the data cannot say which finger was injured."
)
MAP_3D_CAPTION = (
    "Drag to turn the figure, scroll to zoom, and click a region to see its supporting data "
    "below. Pick a tool above to fly to where that tool's injuries concentrate."
)
# The fallback copy names what actually failed: only a browser that reported no WebGL is told
# about WebGL. A blocked CDN, a renderer that failed to start, or an older frontend that sends no
# reason all get the neutral "couldn't load" wording -- blaming WebGL there would misdiagnose it.
WEBGL_FALLBACK_CAPTION = (
    "The 3D body map needs WebGL, which this browser does not provide; showing the flat body "
    "map instead."
)
LOAD_FALLBACK_CAPTION = "Couldn't load the 3D viewer; showing the flat body map instead."
FLAT_VIEW_LABEL = "Flat body map"
FLAT_VIEW_HELP = "Show the flat, keyboard-accessible front/back body map instead of the 3D figure."
BODY3D_KEY = "body_map_3d"
_BODY3D_SEEN_KEY = "body_map_3d_last_event"
_BODY3D_NO_WEBGL_KEY = "body_map_3d_no_webgl"
REGION_OVERRIDE_CAPTION = (
    "The selected body region takes precedence over the sidebar's advanced body-region filter "
    "for this section."
)


def _tool_options_and_labels() -> tuple[list[str], dict[str, str]]:
    tools = load_dims()["tools"]
    options = tools["tool_id"].to_list()
    labels = dict(zip(tools["tool_id"], tools["label"], strict=True))
    return options, labels


def _sync(widget_key: str, persist_key: str) -> None:
    st.session_state[persist_key] = st.session_state[widget_key]


def _selectbox_with_none(
    label: str,
    persist_key: str,
    options: list[str],
    labels: dict[str, str],
    none_label: str,
    caption: str | None = None,
) -> str | None:
    """Persistent-key/widget-key split selectbox (filters.py's pattern) whose first option is
    `None` -- an unset selector always means "all", never an empty result."""
    widget_key = f"widget_{persist_key}"
    st.session_state.setdefault(persist_key, None)
    if widget_key not in st.session_state:
        st.session_state[widget_key] = st.session_state[persist_key]
    st.selectbox(
        label,
        options=[None, *options],
        format_func=lambda v: none_label if v is None else labels.get(v, v),
        key=widget_key,
        on_change=_sync,
        args=(widget_key, persist_key),
    )
    if caption:
        st.caption(caption)
    return st.session_state[persist_key]


def _measure_selectbox() -> str:
    widget_key = "widget_body_map_measure"
    st.session_state.setdefault("body_map_measure", "estimated")
    if widget_key not in st.session_state:
        st.session_state[widget_key] = MEASURES[st.session_state["body_map_measure"]]

    def _sync_measure() -> None:
        st.session_state["body_map_measure"] = _MEASURE_LABEL_TO_ID[st.session_state[widget_key]]

    st.selectbox(
        "Measure", options=list(MEASURES.values()), key=widget_key, on_change=_sync_measure
    )
    return st.session_state["body_map_measure"]


def _measure_filter(flt: CaseFilter, measure: str) -> CaseFilter:
    """D-02/A1: for the 3 diagnosis-subset measures, the measure's own diagnosis category
    REPLACES the page diagnosis selector's constraint for the silhouette -- `CaseFilter` cannot
    express an AND of two category sets."""
    if measure in ("amputation", "laceration", "fracture"):
        return replace(flt, diagnosis_categories=(measure,), diagnosis_basis="any")
    return flt


def _body_scale(values, region_ids: set[str]) -> BodyScale:
    """D-03: one shared scale per render; vmax = max non-suppressed kind='region' value (default
    1.0 if none) -- `stability_badge.resolve`'s `value` is already None for a suppressed row, so
    filtering on non-None is equivalent to filtering on non-suppressed."""
    candidates = [
        rv.value for rid, rv in values.items() if rid in region_ids and rv.value is not None
    ]
    vmax = max(candidates) if candidates else 0.0
    return BodyScale(vmin=0.0, vmax=vmax if vmax > 0 else 1.0)


def _top_n_tool_order(flt: CaseFilter, basis: str, limit: int) -> list[str]:
    """D-07: top `limit` tools by estimated burden (stable-first via
    `stability_badge.order_ranked`) under `flt` -- `flt` here always has `tools=()` (the matrix's
    own tool axis, never restricted to the page's single-tool selection)."""
    df = cached_estimate_by(flt, "tool")
    if df.height == 0:
        return []
    ordered = stability_badge.order_ranked(list(df.iter_rows(named=True)), basis)
    return [row["group_id"] for row in ordered][:limit]


def _narrative_card_html(row: dict) -> str:
    narrative = html.escape(row["narrative"] or "")
    diagnosis = html.escape(row["diagnosis_label"] or "Unknown diagnosis")
    disposition = html.escape(row["disposition_label"] or "Unknown disposition")
    age_bracket = html.escape(row["age_bracket_label"] or "Unknown age bracket")
    meta = f"{row['year']} · {age_bracket} · {diagnosis} · {disposition}"
    return (
        '<div class="wii-narrative-card">'
        f'<div class="wii-narrative-text">{narrative}</div>'
        f'<div class="wii-narrative-meta">{meta}</div>'
        "</div>"
    )


def _load_body3d():
    """The 3D component and its payload builder, or `None` when either cannot be imported -- the
    page then falls back to the flat body map instead of failing (never a blank page)."""
    try:
        from wii.app.body3d_payload import build_body3d_payload
        from wii.app.components.body3d import body3d
    except Exception:  # noqa: BLE001 -- any import failure means "no 3D", never a crash
        return None
    return body3d, build_body3d_payload


def _apply_body3d_event(event: object, current: str | None, valid_ids) -> bool:
    """Feeds a `body3d` return value into the page's single region state
    (`region_select.PERSIST_KEY`) and returns True when the selection changed.

    The real component reports a click as a one-shot trigger (None on later reruns), but a value
    can still read back unchanged on the rerun issued right after it -- and a component that holds
    its last value would return it on every rerun. So an event identical to the last one
    processed, with no empty rerun in between, is not applied again: a re-apply would toggle the
    selection straight back off. A click on the already-selected region clears it, matching
    `region_select.pick_region`; a `None` selection clears it too. An id outside the closed
    region set is ignored, never trusted."""
    if not isinstance(event, dict) or "selected" not in event:
        # No event this rerun (the real component's click is a one-shot trigger that reads back
        # as None afterwards), so the next click -- even on the same region -- is a new one.
        st.session_state.pop(_BODY3D_SEEN_KEY, None)
        return False
    if st.session_state.get(_BODY3D_SEEN_KEY) == event:
        return False
    st.session_state[_BODY3D_SEEN_KEY] = dict(event)
    raw = event.get("selected")
    if raw is None:
        if current is None:
            return False
        region_select.clear_region()
        return True
    picked = region_select.validate_region(raw, valid_ids)
    if picked is None:
        return False
    region_select.pick_region(picked)
    return True


def _render_flat_body_map(values, scale, valid_ids, region_labels, region) -> None:
    if region_select.REGION_CLICK_MODE == "hit_zones":
        # Front and back each need their own keyed container (the hit zones are positioned against
        # one viewBox), so this path renders the pair itself instead of calling render_body_map --
        # which stays exactly as it was for Tool Profiles, Comparison and My Shop.
        front_col, back_col = st.columns(2)
        for column, view in ((front_col, "front"), (back_col, "back")):
            with column:
                render_clickable_body_view(
                    values,
                    scale,
                    instance_id="body-map",
                    view=view,
                    region_ids=valid_ids,
                    labels=region_labels,
                    on_pick=region_select.pick_region,
                    selected_region_id=region,
                )
        st.caption(DEPTH_CAPTION)
    else:
        render_body_map(
            values,
            scale,
            instance_id="body-map",
            selected_region_id=region,
            link_regions=(region_select.REGION_CLICK_MODE == "links"),
        )


def _render_3d_body_map(
    loaded, flt, basis, measure, tool, region, valid_ids, basis_label=""
) -> bool:
    """Renders the 3D figure; returns False when the component raised, so the caller renders the
    flat map in its place. When the browser reports it cannot draw the figure, the reason is
    recorded and the page reruns without mounting the component at all (no empty panel). A region
    click reruns the page once so the figure, the caption and Region detail all reflect the new
    selection."""
    body3d, build_payload = loaded
    try:
        payload = build_payload(
            flt,
            basis,
            measure,
            selected=region,
            active_tool=tool,
            reduced_motion=False,
            basis_label=basis_label,
        )
        event = body3d(payload, key=BODY3D_KEY)
    except Exception:  # noqa: BLE001 -- a broken 3D render degrades to the flat map
        return False
    if isinstance(event, dict) and event.get("webgl") is False:
        # Remember why, then rerun without mounting the component: drawing the flat map below the
        # already-mounted 3D element would leave its empty fixed-height panel above it.
        st.session_state[_BODY3D_NO_WEBGL_KEY] = event.get("reason") or "unknown"
        st.rerun()
    if _apply_body3d_event(event, region, valid_ids):
        st.rerun()
    st.markdown(BODY3D_LEGEND_HTML, unsafe_allow_html=True)
    st.caption(MAP_3D_CAPTION)
    st.caption(DEPTH_CAPTION)
    return True


def _fallback_caption(reason: object) -> str:
    return WEBGL_FALLBACK_CAPTION if reason == "no-webgl" else LOAD_FALLBACK_CAPTION


def _title_block_scope(measure: str, diagnosis: str | None, mechanism: str | None) -> str:
    """The page facts the sidebar chips cannot know: which measure the plate is drawn on, and
    this page's own diagnosis/mechanism selectors. The measure matters to the scope rather than
    to the caption, because the three diagnosis-subset measures REPLACE the diagnosis selector
    for the figure -- so the strip has to say which population was actually drawn."""
    bits = [MEASURES.get(measure, measure)]
    if diagnosis is not None:
        bits.append(f"Diagnosis: {diagnosis.replace('_', ' ').title()}")
    if mechanism is not None:
        bits.append(f"Mechanism: {mechanism.replace('_', ' ').title()}")
    return ", ".join(bits)


def render() -> None:
    st.title(PAGE_TITLE)
    st.caption(PAGE_SUBTITLE)

    # The strip opens the sheet, above the controls, as it does on every other analytical page.
    # It reads this page's own selectors from their persisted keys rather than from the widgets
    # below: a widget's on_change callback writes the persisted key BEFORE the script reruns, so
    # the value read here is always the one the page is about to query with.
    render_title_block(
        DRAWING,
        tools_override=(
            (st.session_state["body_map_tool"],) if st.session_state.get("body_map_tool") else ()
        ),
        scope_extra=_title_block_scope(
            st.session_state.get("body_map_measure", "estimated"),
            st.session_state.get("body_map_diagnosis"),
            st.session_state.get("body_map_mechanism"),
        ),
        latest_year=latest_data_year(),
    )

    # First screenful: the two selectors that change what is DRAWN sit on one row, and the two
    # that only narrow the population go in a Refine expander, so the figure is above the fold.
    tool_options, tool_labels = _tool_options_and_labels()
    dims = load_dims()
    tool_col, measure_col = st.columns(2)
    with tool_col:
        tool = _selectbox_with_none(
            "Tool", "body_map_tool", tool_options, tool_labels, "All workshop tools"
        )
    with measure_col:
        measure = _measure_selectbox()

    diagnosis_options = dims["diagnosis_categories"]["injury_category"].to_list()
    mech_options = mechanism_options()
    with st.expander("Refine", expanded=False):
        diagnosis = _selectbox_with_none(
            "Diagnosis category",
            "body_map_diagnosis",
            diagnosis_options,
            {c: c.replace("_", " ").title() for c in diagnosis_options},
            "All diagnosis categories",
        )
        mechanism = _selectbox_with_none(
            "Mechanism",
            "body_map_mechanism",
            mech_options,
            {m: theme.mechanism_label(m) for m in mech_options},
            "All mechanisms",
            caption=MECHANISM_PROVENANCE_CAPTION,
        )

    basis = st.session_state.get("basis", "annual_average")
    flt = filters.current_filter(tools_override=(tool,) if tool else ())
    if diagnosis is not None:
        flt = replace(flt, diagnosis_categories=(diagnosis,), diagnosis_basis="any")
    if mechanism is not None:
        flt = replace(flt, mechanisms=(mechanism,))

    overall = cached_estimate_total(flt)
    if overall is None or overall.raw_n == 0:
        empty_state(filters.filter_summary())
        return

    dims_all = dims["body_regions_all"]
    valid_ids = {
        row["region_id"] for row in dims_all.iter_rows(named=True) if row["kind"] == "region"
    }
    region_labels = dict(zip(dims_all["region_id"], dims_all["label"], strict=True))

    region_select.sync_from_query_params(valid_ids)

    measure_flt = _measure_filter(flt, measure)
    values = body_map_frame(measure_flt, basis, measure)
    scale = _body_scale(values, valid_ids)

    all_suppressed = all(values.get(rid) is None or values[rid].value is None for rid in valid_ids)
    if all_suppressed:
        st.caption(ALL_SUPPRESSED_CAPTION)
    else:
        # Basis and period are stated once, in the title block at the top of the sheet. Printing
        # `basis_caption` here as well restated both directly above the figure; what is left is
        # the one fact the strip cannot carry -- the any-recorded body basis, which is why these
        # region shares do not sum to 100%. Every region's own reading still carries the full
        # "Estimated U.S. ER-treated injuries -- annual average, YYYY-YYYY" label in its tooltip
        # (`basis_label` below), so the estimate is never a bare number at the point of reading.
        st.caption(BASIS_CAPTION_ANY)
    basis_label = (
        ""
        if measure == "share"
        else basis_caption(basis, *(st.session_state.get("year_range") or (0, 0)))
    )
    if measure in ("amputation", "laceration", "fracture") and diagnosis is not None:
        st.caption(MEASURE_OVERRIDE_CAPTION)
    if measure == "share" and tool is None:
        st.caption(SHARE_ALL_TOOLS_CAPTION)

    region = region_select.selected_region(valid_ids)
    loaded = _load_body3d()
    no_webgl = bool(st.session_state.get(_BODY3D_NO_WEBGL_KEY))
    flat = True
    if loaded is not None and not no_webgl:
        widget_key = "widget_body_map_flat_view"
        st.session_state.setdefault("body_map_flat_view", False)
        if widget_key not in st.session_state:
            st.session_state[widget_key] = st.session_state["body_map_flat_view"]
        st.toggle(
            FLAT_VIEW_LABEL,
            key=widget_key,
            help=FLAT_VIEW_HELP,
            on_change=_sync,
            args=(widget_key, "body_map_flat_view"),
        )
        if not st.session_state["body_map_flat_view"]:
            flat = not _render_3d_body_map(
                loaded, flt, basis, measure, tool, region, valid_ids, basis_label
            )
    if flat:
        if loaded is not None and no_webgl:
            st.caption(_fallback_caption(st.session_state[_BODY3D_NO_WEBGL_KEY]))
        _render_flat_body_map(values, scale, valid_ids, region_labels, region)
    # render_body_map draws its own callout chips; every other path needs them drawn here.
    chips_html = build_callout_chips_html(values)
    if chips_html and not (flat and region_select.REGION_CLICK_MODE != "hit_zones"):
        st.markdown(chips_html, unsafe_allow_html=True)
    st.caption(FINGER_CAPTION)
    if region_select.REGION_CLICK_MODE == "pills":
        region_select.render_region_picker(valid_ids, region_labels)
    region_select.render_clear_control(region_labels)
    if region is None:
        st.caption(NO_REGION_COPY)

    st.header("Region detail")
    flt_region = replace(flt, body_regions=(region,), body_basis="any") if region else flt
    if region is not None and st.session_state.get("f_body_regions"):
        st.caption(REGION_OVERRIDE_CAPTION)

    st.subheader("Diagnosis mix")
    st.caption(profile_bars.DIAGNOSIS_SUBTITLE)
    st.plotly_chart(
        profile_bars.build_diagnosis_bars(injury_mix_frame(flt_region)),
        config=theme.PLOTLY_CONFIG,
        width="stretch",
    )

    st.subheader("Mechanism mix")
    st.caption(MECHANISM_PROVENANCE_CAPTION)
    st.plotly_chart(
        profile_bars.build_mechanism_bars(mechanism_profile_frame(flt_region)),
        config=theme.PLOTLY_CONFIG,
        width="stretch",
    )

    if tool is None and region is not None:
        st.subheader("Top tools")
        top_tools_df = region_top_tools(flt_region, basis)
        if top_tools_df.height:
            # The builder returns None when every row is suppressed: there is no version of that
            # chart worth drawing, so the page prints the one honest line instead.
            fig = ranked_bars.build_ranked_bars(top_tools_df, basis, "Estimated injuries")
            if fig is None:
                st.caption(ranked_bars.all_suppressed_note(top_tools_df, basis, "tools"))
            else:
                st.plotly_chart(fig, config=theme.PLOTLY_CONFIG, width="stretch")

    st.subheader("Representative narratives")
    thresholds = load_thresholds()
    narratives = filtered_narratives(flt_region, thresholds.narrative_min_confidence)
    if narratives.height == 0:
        st.info(ZERO_NARRATIVES_COPY)
    else:
        cards = "".join(_narrative_card_html(row) for row in narratives.iter_rows(named=True))
        st.markdown(f'<div class="wii-narrative-list">{cards}</div>', unsafe_allow_html=True)

    st.header("Body part × tool matrix")
    st.caption(region_tool_matrix.SUBTITLE)
    st.caption(MATRIX_CAPTION)
    st.caption(MATRIX_CALLOUT_NOTE)

    flt_no_tool = replace(flt, tools=())
    matrix_df = region_share_by_tool_frame(flt_no_tool)
    present_tool_ids = sorted(set(matrix_df["tool_id"].to_list())) if matrix_df.height else []
    n_tools = len(present_tool_ids)

    widget_key = "widget_body_map_show_all_tools"
    st.session_state.setdefault("body_map_show_all_tools", False)
    if widget_key not in st.session_state:
        st.session_state[widget_key] = st.session_state["body_map_show_all_tools"]
    st.toggle(
        f"Show all {n_tools} tools" if n_tools else "Show all tools",
        key=widget_key,
        on_change=_sync,
        args=(widget_key, "body_map_show_all_tools"),
    )
    show_all = st.session_state["body_map_show_all_tools"]
    if show_all:
        st.caption(f"Showing all {n_tools} tools.")
    else:
        st.caption("Top 15 tools by estimated burden")

    if matrix_df.height:
        if show_all:
            tool_order = present_tool_ids
        else:
            ranked_order = _top_n_tool_order(flt_no_tool, basis, MATRIX_TOP_N)
            tool_order = [t for t in ranked_order if t in present_tool_ids][:MATRIX_TOP_N]
        region_dims = load_dims()["body_regions"]
        region_order = region_dims["region_id"].to_list()
        region_dim_labels = dict(zip(region_dims["region_id"], region_dims["label"], strict=True))
        tool_label_map = dict(zip(matrix_df["tool_id"], matrix_df["tool_label"], strict=True))
        fig = region_tool_matrix.build_region_tool_matrix(
            matrix_df.filter(pl.col("tool_id").is_in(tool_order)),
            region_order,
            region_dim_labels,
            tool_order,
            tool_label_map,
            show_text=len(tool_order) <= MATRIX_TEXT_MAX_TOOLS,
        )
        st.plotly_chart(fig, config=theme.PLOTLY_CONFIG, width="stretch")


render()
