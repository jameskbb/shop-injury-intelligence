"""PAGE-07 Case Explorer (D-07..D-12): "Read what happened" -- a filterable, searchable list of
de-identified NEISS narratives, with search terms safely highlighted, age brackets (never an exact
age), a high-confidence toggle, and pagination. This page owns EIGHT session keys of its own:
`ce_search`/`ce_high_conf`/`ce_page` plus `ce_f_tools`/`ce_f_subtypes`/`ce_f_mechanisms`/
`ce_f_body_regions`/`ce_f_diagnosis_categories`/`ce_f_dispositions`/`ce_f_age_brackets`/
`ce_f_locations` (each with a `widget_*` twin, Pitfall 4) -- and joins `app.py`'s
`_HIDE_TOOL_PAGES` since it owns its own filter grid rather than the global sidebar's tool filter.
Year range and age scope still come from the global sidebar (D-07); every other filter, the search
box and the high-confidence toggle are page-local. This page shows NO weighted or national
estimate anywhere (D-10) -- every number here is a raw sample count.
"""

from __future__ import annotations

import html
from dataclasses import replace

import streamlit as st

from wii.analytics.insights import load_thresholds
from wii.app import filters as filters_module
from wii.app.components.filter_context import source_field, title_block_html
from wii.app.components.narrative_card import short_disposition
from wii.app.components.pagination import page_window, render_pagination
from wii.app.components.search_highlight import (
    MAX_QUERY_CHARS,
    highlight_narrative,
    parse_search_terms,
)
from wii.app.data import (
    CASE_EXPLORER_PAGE_SIZE,
    case_explorer_count,
    case_explorer_filter_options,
    case_explorer_page,
)
from wii.metrics import CaseFilter

PAGE_TITLE = "Read what happened"
#: This page's title block is built field by field rather than through `render_title_block`,
#: which reads BASIS and SCOPE from the live sidebar state. Neither would be true here: the page
#: shows no weighted estimate at all (so "annual average" is meaningless), and only the sidebar's
#: year range and age scope reach it -- listing its advanced filters would claim a scope this
#: page does not apply.
DRAWING = "Individual ER narratives matching the current filters"
BASIS_FIELD = "Sample cases: no national estimates on this page"
_SCOPE_TAIL = "page filters below"
SEARCH_LABEL = "Search narratives"
SEARCH_PLACEHOLDER = "e.g. kickback, finger, blade guard"
ZERO_RESULTS_COPY = (
    "No cases match the current filters and search terms. Try removing a filter or "
    "shortening your search."
)
HIGH_CONFIDENCE_LABEL = "Only show high-confidence narrative classifications"
SIDEBAR_SCOPE_CAPTION = (
    "The sidebar's year range and age scope apply here; its advanced filters do not -- "
    "use the filters below instead."
)
SUBTYPE_CAPTION = (
    "Most narratives don't name a specific subtype -- leaving this empty includes them."
)
#: Said once, on the page, instead of on every card: the 37-character label "Primary mechanism
#: (narrative-derived)" wrapped the meta grid onto a third row on every result.
MECHANISM_PROVENANCE_CAPTION = (
    "Mechanism is narrative-derived (mined from the ER narrative), not a coded NEISS field; "
    "dispositions are shown in their short clinical form."
)

_PERSIST_FILTER_KEYS = (
    "ce_f_tools",
    "ce_f_subtypes",
    "ce_f_mechanisms",
    "ce_f_body_regions",
    "ce_f_diagnosis_categories",
    "ce_f_dispositions",
    "ce_f_age_brackets",
    "ce_f_locations",
)


def _scope_field() -> str:
    """Only the two sidebar filters that actually reach this page, then a pointer to the page's
    own grid. Built from the same `FilterChip` values the other pages' strips use, so the wording
    matches rather than being re-invented here."""
    bits = [
        chip.value
        for chip in filters_module.active_filter_chips()
        if chip.key in {"year_range", "age_scope"} and chip.value
    ]
    bits.append(_SCOPE_TAIL)
    return " · ".join(bits)


def _render_title_block() -> None:
    from wii.app.data import latest_data_year

    st.markdown(
        title_block_html(DRAWING, BASIS_FIELD, _scope_field(), source_field(latest_data_year())),
        unsafe_allow_html=True,
    )


def _ensure_defaults() -> None:
    st.session_state.setdefault("ce_search", "")
    st.session_state.setdefault("ce_high_conf", False)
    st.session_state.setdefault("ce_page", 1)
    for key in _PERSIST_FILTER_KEYS:
        st.session_state.setdefault(key, ())


def _sync_search() -> None:
    st.session_state["ce_search"] = st.session_state["widget_ce_search"]
    st.session_state["ce_page"] = 1


def _sync_high_conf() -> None:
    st.session_state["ce_high_conf"] = st.session_state["widget_ce_high_conf"]
    st.session_state["ce_page"] = 1


def _sync_filter_and_reset_page(widget_key: str, persist_key: str) -> None:
    st.session_state[persist_key] = tuple(st.session_state[widget_key])
    st.session_state["ce_page"] = 1


def _ce_multiselect(label: str, persist_key: str, options: list, labels: dict | None) -> None:
    """Page-local mirror of `filters._multiselect` (persist-key/widget-key split, Pitfall 4) --
    never imports the private helper. Every change also resets `ce_page` to 1."""
    widget_key = f"widget_{persist_key}"
    if widget_key not in st.session_state:
        st.session_state[widget_key] = list(st.session_state.get(persist_key, ()))
    fmt = (lambda v: labels.get(v, v)) if labels else (lambda v: v)
    st.multiselect(
        label,
        options=options,
        key=widget_key,
        format_func=fmt,
        on_change=_sync_filter_and_reset_page,
        args=(widget_key, persist_key),
    )


def _mechanism_display(key: str | None) -> str:
    if not key or key == "unknown":
        return "Unknown mechanism"
    if key == "other":
        return "Other (classified)"
    return key.replace("_", " ").title()


def _case_card_html(row: dict, terms: tuple[str, ...], threshold: float) -> str:
    """Single-string, no-blank-line HTML for one result card (Pitfall 3). The narrative is emitted
    ONLY via `highlight_narrative` -- every other field is `html.escape`d directly. Missing values
    fall back to the dimension's "Unknown ..." label, never a blank field (E7 partial)."""
    tool = html.escape(row["tool_label"] or "Unknown tool")
    year = html.escape(str(row["year"]))
    age_bracket = html.escape(row["age_bracket_label"] or "Unknown age bracket")
    body_part = html.escape(row["body_part_label"] or "Unknown body part")
    diagnosis = html.escape(row["diagnosis_label"] or "Unknown diagnosis")
    # The full NEISS disposition label runs to ~150 characters and wrapped every card onto two
    # extra rows; the short clinical form is the shared one from `components.narrative_card`.
    disposition = html.escape(short_disposition(row["disposition_label"]))
    mechanism = html.escape(_mechanism_display(row["primary_mechanism"]))
    confidence = row["confidence"]
    # The classifier's self-score is the least useful field on the card, so a high score is plain
    # muted text. Only a LOW one is marked, because only a low one is a caution.
    if confidence is None:
        badge_class = "wii-confidence-low"
        confidence_text = "Unknown"
    elif confidence >= threshold:
        badge_class = "wii-confidence-high"
        confidence_text = "High"
    else:
        badge_class = "wii-confidence-low"
        confidence_text = f"Low · {confidence:.2f}"
    confidence_html = html.escape(confidence_text)
    narrative_html = highlight_narrative(row["narrative"], terms)
    meta = (
        f'<span class="wii-case-meta-label">Tool</span> '
        f'<span class="wii-case-meta-value">{tool}</span>'
        f'<span class="wii-case-meta-label">Year</span> '
        f'<span class="wii-case-meta-value">{year}</span>'
        f'<span class="wii-case-meta-label">Age bracket</span> '
        f'<span class="wii-case-meta-value">{age_bracket}</span>'
        f'<span class="wii-case-meta-label">Body part</span> '
        f'<span class="wii-case-meta-value">{body_part}</span>'
        f'<span class="wii-case-meta-label">Diagnosis</span> '
        f'<span class="wii-case-meta-value">{diagnosis}</span>'
        f'<span class="wii-case-meta-label">Disposition</span> '
        f'<span class="wii-case-meta-value">{disposition}</span>'
        f'<span class="wii-case-meta-label">Mechanism</span> '
        f'<span class="wii-case-meta-value">{mechanism}</span>'
        f'<span class="wii-case-meta-label">Confidence</span> '
        f'<span class="{badge_class}">{confidence_html}</span>'
    )
    return (
        '<div class="wii-case-card">'
        f'<div class="wii-case-narrative">{narrative_html}</div>'
        f'<div class="wii-case-meta">{meta}</div>'
        "</div>"
    )


def _clean(persist_key: str, valid: set) -> tuple:
    current = tuple(st.session_state.get(persist_key, ()))
    cleaned = tuple(v for v in current if v in valid)
    if cleaned != current:
        st.session_state[persist_key] = cleaned
    return cleaned


def render() -> None:
    _ensure_defaults()
    st.title(PAGE_TITLE)
    _render_title_block()

    options = case_explorer_filter_options()

    def opt_values(key: str) -> list:
        return [v for v, _ in options[key]]

    def opt_labels(key: str) -> dict:
        return {v: lbl for v, lbl in options[key]}

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        _ce_multiselect("Tool", "ce_f_tools", opt_values("tools"), opt_labels("tools"))
    with col2:
        _ce_multiselect(
            "Primary mechanism (narrative-derived)",
            "ce_f_mechanisms",
            opt_values("mechanisms"),
            opt_labels("mechanisms"),
        )
    with col3:
        _ce_multiselect(
            "Body region",
            "ce_f_body_regions",
            opt_values("body_regions"),
            opt_labels("body_regions"),
        )
    with col4:
        _ce_multiselect(
            "Diagnosis category",
            "ce_f_diagnosis_categories",
            opt_values("diagnosis_categories"),
            opt_labels("diagnosis_categories"),
        )

    with st.expander("More filters", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _ce_multiselect(
                "Narrative-refined subtype",
                "ce_f_subtypes",
                opt_values("tool_subtypes"),
                opt_labels("tool_subtypes"),
            )
            st.caption(SUBTYPE_CAPTION)
        with c2:
            _ce_multiselect(
                "Disposition",
                "ce_f_dispositions",
                opt_values("dispositions"),
                opt_labels("dispositions"),
            )
        with c3:
            _ce_multiselect(
                "Age bracket",
                "ce_f_age_brackets",
                opt_values("age_brackets"),
                opt_labels("age_brackets"),
            )
        with c4:
            _ce_multiselect(
                "Location", "ce_f_locations", opt_values("locations"), opt_labels("locations")
            )

    if "widget_ce_search" not in st.session_state:
        st.session_state["widget_ce_search"] = st.session_state["ce_search"]
    st.text_input(
        SEARCH_LABEL,
        max_chars=MAX_QUERY_CHARS,
        placeholder=SEARCH_PLACEHOLDER,
        key="widget_ce_search",
        on_change=_sync_search,
    )
    st.caption(SIDEBAR_SCOPE_CAPTION)
    st.caption(MECHANISM_PROVENANCE_CAPTION)

    thresholds = load_thresholds()
    if "widget_ce_high_conf" not in st.session_state:
        st.session_state["widget_ce_high_conf"] = st.session_state["ce_high_conf"]
    st.toggle(HIGH_CONFIDENCE_LABEL, key="widget_ce_high_conf", on_change=_sync_high_conf)
    st.caption(
        f"Confidence ≥ {thresholds.narrative_min_confidence:.2f} from the narrative "
        "classifier; default off shows all classified cases."
    )

    terms = parse_search_terms(st.session_state["ce_search"])
    min_confidence = (
        thresholds.narrative_min_confidence if st.session_state["ce_high_conf"] else None
    )

    year_range = st.session_state.get("year_range")
    base = CaseFilter(
        years=tuple(year_range) if year_range else None,
        age_scope=st.session_state.get("age_scope", "adults"),
    )
    flt = replace(
        base,
        tools=_clean("ce_f_tools", set(opt_values("tools"))),
        tool_subtypes=_clean("ce_f_subtypes", set(opt_values("tool_subtypes"))),
        mechanisms=_clean("ce_f_mechanisms", set(opt_values("mechanisms"))),
        body_regions=_clean("ce_f_body_regions", set(opt_values("body_regions"))),
        diagnosis_categories=_clean(
            "ce_f_diagnosis_categories", set(opt_values("diagnosis_categories"))
        ),
        dispositions=_clean("ce_f_dispositions", set(opt_values("dispositions"))),
        age_brackets=_clean("ce_f_age_brackets", set(opt_values("age_brackets"))),
        locations=_clean("ce_f_locations", set(opt_values("locations"))),
    )

    total = case_explorer_count(flt, terms, min_confidence)
    st.subheader(f"{total:,} matching sample cases (not a national estimate)")

    if total == 0:
        st.info(ZERO_RESULTS_COPY)
        return

    window = page_window(total, st.session_state.get("ce_page", 1), CASE_EXPLORER_PAGE_SIZE)
    st.session_state["ce_page"] = window.page

    rows = case_explorer_page(flt, terms, window.page, min_confidence)
    cards = "".join(
        _case_card_html(row, terms, thresholds.narrative_min_confidence)
        for row in rows.iter_rows(named=True)
    )
    st.markdown(f'<div class="wii-case-list">{cards}</div>', unsafe_allow_html=True)

    render_pagination(window, "ce_page")


render()
