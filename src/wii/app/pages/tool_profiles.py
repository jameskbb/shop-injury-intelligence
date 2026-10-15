"""PAGE-02 Tool Profiles (D-10). This page owns its OWN tool selection under the
`profile_selected_tool` session-state key -- distinct from the global sidebar's
`global_tool_filter` key, which app.py hides on this page (D-03). Section order per UI-SPEC:
tool selector -> header -> KPI row -> INJURY TREND -> ANATOMY SNAPSHOT -> diagnosis/mechanism/
disposition profiles -> WHAT HAPPENS WITH THIS TOOL? -> REPRESENTATIVE CASE PATTERNS -> footer
(rendered globally by app.py).
"""

from __future__ import annotations

from dataclasses import replace

import polars as pl
import streamlit as st

from wii.analytics.insights import generate_insights, load_thresholds
from wii.app import filters, theme
from wii.app.charts import profile_bars, trend
from wii.app.components.body_map import VIEWS_FRONT, BodyScale, RegionValue, render_body_map
from wii.app.components.filter_context import render_title_block
from wii.app.components.insight_card import render_insight_section
from wii.app.components.kpi_card import KpiCard, render_kpi_strip
from wii.app.components.narrative_card import narrative_list_html
from wii.app.components.stability_badge import (
    STATE_STABLE,
    STATE_SUPPRESSED,
    basis_caption,
    empty_state,
    format_share,
    resolve,
    resolve_share,
    suppressed_share_row,
)
from wii.app.data import (
    TREND_BASIS,
    anatomy_snapshot_frame,
    available_years,
    cached_estimate_by,
    cached_estimate_total,
    cached_severity_profile,
    cached_share_by,
    default_tool_id,
    disposition_profile_rows,
    injury_mix_frame,
    insight_frames,
    load_dims,
    mechanism_profile_frame,
    median_age,
    representative_narratives,
    trend_frame,
)
from wii.metrics import CaseFilter

_ANATOMY_SUBTITLE = (
    "Body regions are normalized within the selected tool so anatomical patterns can be compared."
)

_NARRATIVE_SECTION_CAPTION = (
    "Up to 5 high-confidence ER narratives for this tool's top mechanisms, shown "
    "verbatim-as-coded for pattern illustration -- coded clinical framing, not dramatized."
)
_NARRATIVE_EMPTY_COPY = (
    "No high-confidence narrative examples available for this tool with the current filters."
)
_TOP_MECHANISM_COUNT = 3

#: Selector suffix for a tool with no reportable burden estimate under the current filter/basis.
_NO_ESTIMATE_SUFFIX = "(no reportable estimate)"

_UNREPORTABLE_HEADING = "No reportable estimate for this tool"
#: Stated once, above whichever lever applies. Deliberately about the SAMPLE, never about the
#: tool: "too few NEISS cases" is a fact about surveillance coverage, and a reader must not be
#: able to walk away from this page thinking a thin sample means a safe tool.
_UNREPORTABLE_BODY = (
    "NEISS recorded {raw_n:,} sample cases for {tool} under the current filters, below the CPSC "
    "reporting threshold, so no national estimate can be published for it. That is a limit of the "
    "surveillance sample, **not** a finding that this tool is safer: a tool can be absent here "
    "because it is rarely owned, rarely used, or simply rarely coded."
)
_UNREPORTABLE_BASIS_LEVER = (
    "**Pooled over the whole period it does clear the threshold.** Switch **Estimate basis** to "
    "**Period total** in the sidebar to see it."
)
_UNREPORTABLE_YEARS_LEVER = (
    "**Over the full {start}–{end} record it does clear the threshold.** Widen the **Year range** "
    "in the sidebar to see it."
)
_UNREPORTABLE_NO_LEVER = (
    "This tool stays below the threshold across every year and basis in the dataset, so no filter "
    "will produce an estimate for it."
)

_ESTIMATE_TOOLTIP = (
    "Survey-weighted national estimate of ER-treated injuries associated with this tool, within "
    "the NEISS reporting population. A burden, not a rate: NEISS counts no tool owners and no "
    "hours of use, so this cannot be divided into a per-use figure."
)
_SHARE_OF_BURDEN_TOOLTIP = (
    "How much of the estimated workshop-wide ER burden this one tool accounts for under the "
    "current filters. The burden half of the picture; the shares beside it describe severity "
    "among this tool's own ER cases."
)
_ESCALATED_TOOLTIP = (
    "Share of this tool's estimated ER-treated injuries whose visit ended in admission or "
    "transfer rather than release. Severity among ER cases, never a probability of injury."
)


def _reportable_tool_ids(workshop_flt: CaseFilter, basis: str) -> set[str]:
    """Tool ids whose OWN burden estimate clears the CPSC gate under the current filter and basis.

    Reads the same `cached_estimate_by(flt, "tool")` frame `default_tool_id` already pulls on this
    page, so marking the selector costs no extra query.
    """
    return {
        row["group_id"]
        for row in cached_estimate_by(workshop_flt, "tool").iter_rows(named=True)
        if resolve(row, basis).state == STATE_STABLE
    }


def _tool_options_and_labels(
    reportable: set[str],
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Returns (ordered tool_id options, selector display labels incl. the "(narrative-refined)"
    suffix per D-10, and plain header labels without the suffix).

    All 31 tools stay in the list and stay selectable. Which of them currently have no reportable
    estimate is a fact about the data, so it is ANNOTATED rather than used to remove options: a
    reader who came looking for the lathe should learn that NEISS is too thin there, not find the
    lathe quietly missing. Pruning would also make the option list a function of filter state,
    which `filters.OPTIONS_DO_NOT_CASCADE` rules out -- and which that same note sanctions doing
    this way, by re-labelling rather than subtracting.
    """
    tools = load_dims()["tools"]
    options = tools["tool_id"].to_list()
    selector_labels: dict[str, str] = {}
    plain_labels: dict[str, str] = {}
    for row in tools.iter_rows(named=True):
        tool_id = row["tool_id"]
        plain_labels[tool_id] = row["label"]
        label = row["label"]
        if row["source"] == "narrative_refined":
            label = f"{label} (narrative-refined)"
        if tool_id not in reportable:
            label = f"{label} {_NO_ESTIMATE_SUFFIX}"
        selector_labels[tool_id] = label
    return options, selector_labels, plain_labels


def _ensure_default_tool(options: list[str]) -> None:
    """D-10 backstop: pre-selects a computed default tool on first load (or if a stale selection
    no longer exists among `options`) -- never an empty profile."""
    current = st.session_state.get("profile_selected_tool")
    if current in options:
        return
    basis = st.session_state.get("basis", "annual_average")
    workshop_flt = filters.current_filter(tools_override=())
    default = default_tool_id(workshop_flt, basis)
    st.session_state["profile_selected_tool"] = default if default in options else options[0]


def _metric_row(profile, metric_id: str) -> dict:
    rows = profile.filter(profile["metric_id"] == metric_id)
    if rows.height == 0:
        return suppressed_share_row()
    return rows.row(0, named=True)


def _estimate_card(flt: CaseFilter, basis: str) -> KpiCard:
    overall = cached_estimate_total(flt)
    resolved = resolve(overall, basis)
    caption = basis_caption(basis, flt.years[0], flt.years[1]) if flt.years else ""
    if resolved.label:
        caption = f"{caption} ({resolved.label})" if caption else resolved.label
    return KpiCard(
        label="Estimated injuries",
        value=resolved.display_value,
        caption=caption,
        hero=True,
        dimension=f"{flt.years[0]}–{flt.years[1]}" if flt.years else "",
        tooltip=_ESTIMATE_TOOLTIP,
    )


def _share_of_burden_card(workshop_flt: CaseFilter, tool_id: str) -> KpiCard:
    tool_flt = replace(workshop_flt, tools=(tool_id,))
    share_df = cached_share_by(workshop_flt, tool_flt, by="all")
    row = share_df.row(0, named=True) if share_df.height else suppressed_share_row()
    resolved = resolve_share(row)
    caption = "share of estimated U.S. ER-treated injuries, workshop-wide"
    if resolved.label:
        caption = f"{caption} ({resolved.label})"
    return KpiCard(
        label="Share of workshop burden",
        value=resolved.display_value,
        caption=caption,
        tooltip=_SHARE_OF_BURDEN_TOOLTIP,
    )


def workshop_baseline_text(workshop_profile, metric_id: str) -> str:
    """ "workshop 5.4%" -- the comparator that makes a within-tool share mean anything.

    This page's whole thesis is "what does this tool's ER profile look like against the
    workshop", and four within-tool shares shipped as bare numbers: 12.2% amputation is only a
    finding once you know the workshop runs at 5.4%. The baseline goes in the card's comparison
    slot, monochrome, with no glyph -- it is a level, not a movement, and asserting a direction
    would turn a descriptive difference into a safety claim NEISS cannot support.

    Returns "" when the workshop figure is not itself stable: an unstable comparator is worse
    than none, because the reader would anchor on it.
    """
    resolved = resolve_share(_metric_row(workshop_profile, metric_id))
    if resolved.state != STATE_STABLE or resolved.value is None:
        return ""
    return f"workshop {format_share(resolved.value)}"


def _share_metric_card(
    label: str,
    profile,
    metric_id: str,
    *,
    severe: bool = False,
    workshop_profile=None,
    tooltip: str = "",
) -> KpiCard:
    resolved = resolve_share(_metric_row(profile, metric_id))
    caption = "share among this tool's ER cases"
    if resolved.label:
        caption = f"{caption} ({resolved.label})"
    baseline = (
        workshop_baseline_text(workshop_profile, metric_id) if workshop_profile is not None else ""
    )
    return KpiCard(
        label=label,
        value=resolved.display_value,
        caption=caption,
        severe=severe,
        baseline=baseline,
        tooltip=tooltip,
    )


def _mechanism_kpi_card(mech_df: pl.DataFrame) -> KpiCard:
    """Highest STABLE non-"unknown" mechanism share for the tool.

    The VALUE is the share and the CAPTION names the mechanism, not the other way round. With the
    label in the value slot, "Direct Cutting Contact" set at the measured-value size wrapped to
    two lines, became the loudest object on the strip and broke every other card's value
    baseline -- a category name is not a measured quantity and should not be typeset as one.
    """
    if mech_df.height == 0:
        candidates = mech_df
    else:
        candidates = mech_df.filter(
            (pl.col("state") == "stable") & (pl.col("category") != "unknown")
        )
    if candidates.height == 0:
        return KpiCard(
            label="Primary mechanism",
            value="—",
            caption="No stable mechanism data for this tool",
        )
    top = candidates.sort("value", descending=True).row(0, named=True)
    return KpiCard(
        label="Primary mechanism",
        value=format_share(top["value"]),
        caption=f"{top['label']}: most common mechanism, narrative-derived",
        tooltip=(
            "The highest-share mechanism with a reportable estimate for this tool. Mechanisms "
            "are classified from ER narrative text, not a coded NEISS field."
        ),
    )


def _median_age_card(flt: CaseFilter, raw_n: int, workshop_flt: CaseFilter) -> KpiCard | None:
    """Shown ONLY when raw N >= 100 -- the card is omitted (not blank) below that. A median age
    reads only against another median, so the workshop's own sits in the comparison slot."""
    if raw_n < 100:
        return None
    age = median_age(flt)
    if age is None:
        return None
    workshop_age = median_age(workshop_flt)
    baseline = f"workshop median {workshop_age:.0f}" if workshop_age is not None else ""
    return KpiCard(
        label="Median age",
        value=f"{age:.0f}",
        caption="median age of sample cases (unweighted)",
        baseline=baseline,
    )


def _unreportable_lever(flt: CaseFilter, basis: str) -> str:
    """The ONE sidebar move that would give this tool an estimate, or the honest statement that
    there isn't one.

    Both branches are tested against the data rather than guessed: the page re-resolves this
    tool's own estimate on the other basis, and (failing that) over the full year range. Nine of
    the eleven tools that come up empty at the default filter are empty because the 1,200 floor is
    being applied to an ANNUAL average, not because the sample is thin -- telling those readers to
    "try widening the filters" would send them hunting for a toggle we already know the answer to.
    """
    overall = cached_estimate_total(flt)
    if overall is not None and basis == "annual_average":
        if resolve(overall, "period_total").state == STATE_STABLE:
            return _UNREPORTABLE_BASIS_LEVER
    years = available_years()
    if years and flt.years != (years[0], years[-1]):
        widened = cached_estimate_total(replace(flt, years=(years[0], years[-1])))
        if widened is not None and resolve(widened, basis).state == STATE_STABLE:
            return _UNREPORTABLE_YEARS_LEVER.format(start=years[0], end=years[-1])
    return _UNREPORTABLE_NO_LEVER


def _render_unreportable(tool_label: str, raw_n: int, flt: CaseFilter, basis: str) -> None:
    """What this page shows instead of a full dashboard when the tool's own headline estimate is
    suppressed.

    Until now the only bail-out was `raw_n == 0`; one sample case was enough to render the whole
    page, so Shaper (9 cases in 20 years) drew eight "—" KPIs, four empty charts and a body plate
    captioned "22 of 22 body regions are below the reporting floor". Two screens of furniture
    around no number is worse than no page: it reads as a broken dashboard rather than as an
    honest absence, and it buries the one line that actually explains what happened.
    """
    st.subheader(_UNREPORTABLE_HEADING)
    st.markdown(_UNREPORTABLE_BODY.format(raw_n=raw_n, tool=tool_label))
    st.markdown(_unreportable_lever(flt, basis))


def _anatomy_scale(values: dict[str, RegionValue], region_ids: set[str]) -> BodyScale:
    candidates = [
        rv.value for rid, rv in values.items() if rid in region_ids and rv.value is not None
    ]
    vmax = max(candidates) if candidates else 0.0
    return BodyScale(vmin=0.0, vmax=vmax if vmax > 0 else 1.0)


def _render_anatomy_snapshot(flt: CaseFilter, basis: str, tool_id: str) -> None:
    st.header("Anatomy snapshot")
    st.caption(_ANATOMY_SUBTITLE)
    values = anatomy_snapshot_frame(flt, basis)
    dims = load_dims()["body_regions_all"]
    region_ids = {row["region_id"] for row in dims.iter_rows(named=True) if row["kind"] == "region"}
    scale = _anatomy_scale(values, region_ids)
    # Front plate only. The back view repeated the front's single finding down another 460px of
    # page, and on every tool whose cases concentrate in the hand it carried no stable region at
    # all -- half a screen of page height saying nothing.
    # `confidence_outlines=False`: per-region dashes are for the full-size Body Map figure alone.
    # For a tool whose cases concentrate in the hand -- most of them -- 20 of 22 regions fall
    # below the floor, so the dashes outnumber the inked regions about 10 to 1 and stop being a
    # line treatment: the plate reads as a dashed skeleton with two blue hands, which is the
    # amber-hatch inversion in a new medium. The floor count carries the fact in words below, and
    # every region keeps its own `<title>` stability.
    render_body_map(
        values,
        scale,
        instance_id=f"tool-profile-{tool_id}",
        views=VIEWS_FRONT,
        region_ids=region_ids,
        confidence_outlines=False,
    )


def _render_trend(flt: CaseFilter) -> None:
    """The sidebar basis does not reach this chart. Every point is ONE treatment year, so the
    only honest basis for it is that year's own total; under "annual average" the y axis said
    "Estimated annual average" over points that were each a single year's estimate divided by
    the twenty years in the domain. `data.TREND_BASIS` is the single place that rule lives."""
    st.header("Injury trend")
    st.caption(trend.SUBTITLE)
    df = trend_frame(flt)
    fig = trend.build_trend(df, TREND_BASIS)
    st.plotly_chart(fig, config=theme.PLOTLY_CONFIG, width="stretch")
    years_present = df["year"].to_list() if df.height else []
    if years_present and min(years_present) < trend.SCHEMA_CHANGE_YEAR <= max(years_present):
        st.caption(trend.SCHEMA_CHANGE_CAPTION)


def _render_diagnosis_mechanism_disposition(
    flt: CaseFilter, mech_df: pl.DataFrame, workshop_flt: CaseFilter
) -> None:
    """Every share bar here carries the all-workshop share as a ghost tick, so "74.6% laceration"
    is read against the workshop's own figure at the point of looking rather than by scrolling
    back to a KPI. That comparator is the page's whole thesis and it used to exist only in the
    insight cards at the bottom."""
    st.header("Diagnosis, mechanism & disposition")
    st.subheader("Diagnosis profile")
    st.caption(profile_bars.DIAGNOSIS_SUBTITLE)
    st.plotly_chart(
        profile_bars.build_diagnosis_bars(injury_mix_frame(flt, workshop_flt)),
        config=theme.PLOTLY_CONFIG,
        width="stretch",
    )
    st.subheader("Mechanism profile")
    st.caption(profile_bars.MECHANISM_SUBTITLE)
    st.plotly_chart(
        profile_bars.build_mechanism_bars(mech_df),
        config=theme.PLOTLY_CONFIG,
        width="stretch",
    )
    st.subheader("Disposition")
    st.caption(profile_bars.DISPOSITION_CAPTION)
    st.plotly_chart(
        profile_bars.build_disposition_stack(disposition_profile_rows(flt)),
        config=theme.PLOTLY_CONFIG,
        width="stretch",
    )


def _render_tool_insights(workshop_flt: CaseFilter, basis: str, tool_id: str) -> None:
    """`insight_frames` is built over the WORKSHOP-WIDE filter (never the tool-restricted one) so
    every tool remains a candidate internally -- `generate_insights(..., scope_tool=tool_id)`
    filters the ranked output down to this tool afterward (03-02's documented reuse pattern)."""
    st.header("What happens with this tool?")
    st.caption(
        "Deterministic patterns that clear stability and effect-size thresholds for this tool "
        "-- never padded with weaker statements."
    )
    thresholds = load_thresholds()
    frames = insight_frames(workshop_flt, basis)
    insights = generate_insights(frames, thresholds, basis, scope_tool=tool_id)
    render_insight_section(insights, thresholds.min_cards_for_section)


def _render_representative_narratives(flt: CaseFilter, tool_id: str, mech_df: pl.DataFrame) -> None:
    st.header("Representative case patterns")
    st.caption(_NARRATIVE_SECTION_CAPTION)
    thresholds = load_thresholds()
    # mechanism_profile_frame() returns a column-less frame when no mechanism has a row.
    if mech_df.height == 0:
        top_mechanisms: tuple[str, ...] = ()
    else:
        candidates = mech_df.filter(
            (pl.col("state") == "stable") & (pl.col("category") != "unknown")
        ).sort("value", descending=True)
        top_mechanisms = tuple(candidates["category"].to_list()[:_TOP_MECHANISM_COUNT])
    narratives = representative_narratives(
        flt, tool_id, top_mechanisms, thresholds.narrative_min_confidence, limit=5
    )
    if narratives.height == 0:
        st.info(_NARRATIVE_EMPTY_COPY)
        return
    st.markdown(narrative_list_html(narratives.iter_rows(named=True)), unsafe_allow_html=True)


def render() -> None:
    basis = st.session_state.get("basis", "annual_average")
    workshop_flt = filters.current_filter(tools_override=())
    options, selector_labels, plain_labels = _tool_options_and_labels(
        _reportable_tool_ids(workshop_flt, basis)
    )
    if not options:
        st.warning("No tools are available in the committed dataset.")
        return

    _ensure_default_tool(options)
    st.selectbox(
        "Select a tool",
        options=options,
        format_func=lambda tid: selector_labels.get(tid, tid),
        key="profile_selected_tool",
    )
    tool_id = st.session_state["profile_selected_tool"]
    tool_label = plain_labels.get(tool_id, tool_id)

    st.title(f"{tool_label} Injury Profile")
    # Every analytical page opens with the strip, not only Overview: this page shipped a
    # five-figure estimate with the period, population and basis it was computed under stated
    # nowhere above the fold.
    render_title_block(
        f"{tool_label}: injury profile against the workshop",
        tools_override=(tool_id,),
    )

    flt = filters.current_filter(tools_override=(tool_id,))
    overall = cached_estimate_total(flt)
    if overall is None or overall.raw_n == 0:
        empty_state(filters.filter_summary())
        return
    # A suppressed headline means every section below it is "—" as well: the KPI strip, all four
    # charts and the body plate are all driven by this same estimate. Say so once, with the lever.
    if resolve(overall, basis).state == STATE_SUPPRESSED:
        _render_unreportable(tool_label, overall.raw_n, flt, basis)
        return

    profile = cached_severity_profile(flt, by="tool")
    profile = profile.filter(profile["group_id"] == tool_id)
    # `by="all"` pools the whole filtered workshop into one row, so every card below compares
    # this tool against the SAME workshop scope the reader's filters describe -- never against a
    # fixed all-years figure that would silently contradict the title block.
    workshop_profile = cached_severity_profile(workshop_flt, by="all")
    mech_df = mechanism_profile_frame(flt, workshop_flt)

    kpi_cards = [
        _estimate_card(flt, basis),
        _share_of_burden_card(workshop_flt, tool_id),
        _share_metric_card(
            "Escalated-care share",
            profile,
            "escalated_care",
            severe=True,
            workshop_profile=workshop_profile,
            tooltip=_ESCALATED_TOOLTIP,
        ),
        _share_metric_card(
            "Amputation share", profile, "amputation", workshop_profile=workshop_profile
        ),
        _share_metric_card(
            "Laceration share", profile, "laceration", workshop_profile=workshop_profile
        ),
        _share_metric_card(
            "Hand/finger share", profile, "hand_finger", workshop_profile=workshop_profile
        ),
        _mechanism_kpi_card(mech_df),
    ]
    median_card = _median_age_card(flt, overall.raw_n, workshop_flt)
    if median_card is not None:
        kpi_cards.append(median_card)
    render_kpi_strip(kpi_cards)

    _render_trend(flt)
    _render_anatomy_snapshot(flt, basis, tool_id)
    _render_diagnosis_mechanism_disposition(flt, mech_df, workshop_flt)
    _render_tool_insights(workshop_flt, basis, tool_id)
    _render_representative_narratives(flt, tool_id, mech_df)


render()
