"""D-05 region_select: the single source of truth for the Body Map's selected body region. Pure
component -- imports streamlit and typing/html only, never `wii.app.data` or `wii.metrics`. The
caller (`src/wii/app/pages/body_map.py`, 04-04) passes the closed valid-id set (dim_body_region
`kind='region'`) and a label mapping; this module owns nothing about metrics or data.

D-05 Wave-0 spike verdict (scripts/spikes/spike_st_query_params_click.py, run against the
PRODUCTION `body_map.build_body_svg(..., link_regions=True)` renderer, Streamlit 1.63.0,
2026-09-15, reproduced identically on 3 runs): "click_ok_session_lost" -- the `?region=` query
param DOES arrive after a real browser click on the link-wrapped SVG, but `st.session_state` is
reset on the resulting full-page navigation (confirms 04-RESEARCH.md Pitfall 3's community-forum
risk). Per D-05's decision rule, only "click_ok_session_preserved" makes the
`<a href="?region=...">` link mechanism primary -- any other verdict, including this one, means
REGION_CLICK_MODE is "pills": `st.pills` is the PRIMARY selector here, not a fallback bolted on
after the fact. `sync_from_query_params` still runs in BOTH modes so a `?region=` deep link (a
bookmarked/shared URL) keeps working even though clicking the body map itself uses pills.
"""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

SPIKE_VERDICT = "click_ok_session_lost"

# 2026-09-16 follow-up spike, same question, different mechanism. The 2026-09-15 verdict above is
# still correct for what it measured -- an `<a href="?region=">` click IS a full browser
# navigation, so a new Streamlit session and a reset `session_state` are unavoidable there, not a
# bug. What was wrong was generalising "anchors lose state" into "click-on-body is impossible",
# which is how a 22-item pill row became the primary selector for a body map.
#
# Invisible per-region `st.button` hit zones layered under a `pointer-events:none` SVG select via
# an ordinary widget rerun, with no navigation at all. Measured across all 22 front regions: every
# click selected the region under the cursor, hit-zone alignment against the real shape boxes
# deviated by 0.0px, and a session sentinel (a counter set before the clicks) survived every one.
HIT_ZONE_VERDICT = "click_ok_session_preserved"

REGION_CLICK_MODE = (
    "hit_zones"
    if HIT_ZONE_VERDICT == "click_ok_session_preserved"
    else "links"
    if SPIKE_VERDICT == "click_ok_session_preserved"
    else "pills"
)

PERSIST_KEY = "body_map_region"
WIDGET_KEY = "widget_body_map_region"
QUERY_PARAM = "region"


def validate_region(value: Any, valid_ids) -> str | None:
    """Returns `value` only if it is a `str` exactly present in `valid_ids` -- never a
    case-insensitive or fuzzy match. Anything else (wrong type, unknown id) is "no selection",
    never an error (T-04-01: rejects `"<script>"`, wrong-case, list/None inputs)."""
    if not isinstance(value, str):
        return None
    if value not in valid_ids:
        return None
    return value


def sync_from_query_params(valid_ids) -> None:
    """Runs in BOTH modes so a `?region=` deep link works regardless of REGION_CLICK_MODE. A
    present-and-valid value is written to PERSIST_KEY (and, in pills mode, also seeds WIDGET_KEY
    so the picker reflects the deep link). A present-but-invalid value clears PERSIST_KEY and
    removes the query param -- an unvalidated value never reaches markup/SQL (T-04-01). An
    absent param leaves the persisted selection unchanged."""
    raw = st.query_params.get(QUERY_PARAM)
    if raw is None:
        return
    validated = validate_region(raw, valid_ids)
    if validated is None:
        st.session_state[PERSIST_KEY] = None
        del st.query_params[QUERY_PARAM]
        return
    st.session_state[PERSIST_KEY] = validated
    if REGION_CLICK_MODE == "pills":
        st.session_state[WIDGET_KEY] = validated


def selected_region(valid_ids) -> str | None:
    """The validated PERSIST_KEY value -- re-validates on every read so a stale/tampered
    `session_state` value can never leak into a caller unvalidated."""
    return validate_region(st.session_state.get(PERSIST_KEY), valid_ids)


def clear_region() -> None:
    """`on_click` callback: resets PERSIST_KEY and WIDGET_KEY to `None` and removes ONLY the
    QUERY_PARAM key -- never `st.query_params.clear()`, which would drop unrelated params a
    future page might add."""
    st.session_state[PERSIST_KEY] = None
    if WIDGET_KEY in st.session_state:
        st.session_state[WIDGET_KEY] = None
    if QUERY_PARAM in st.query_params:
        del st.query_params[QUERY_PARAM]


def _sync_widget_to_persist() -> None:
    st.session_state[PERSIST_KEY] = st.session_state[WIDGET_KEY]


def pick_region(region_id: str) -> None:
    """`on_click` callback for a hit-zone button. Clicking the already-selected region clears it,
    so the same target both selects and deselects and no separate affordance is needed to get back
    to "all regions".

    `region_id` always originates from the committed asset's own element ids, never from user
    input, but it is validated on read by `selected_region` regardless. The query param is
    rewritten from Python so the URL stays shareable -- a Python-side assignment, which does not
    reload the page and therefore does not lose session state.
    """
    current = st.session_state.get(PERSIST_KEY)
    new_value = None if current == region_id else region_id
    st.session_state[PERSIST_KEY] = new_value
    if WIDGET_KEY in st.session_state:
        st.session_state[WIDGET_KEY] = new_value
    if new_value is None:
        if QUERY_PARAM in st.query_params:
            del st.query_params[QUERY_PARAM]
    else:
        st.query_params[QUERY_PARAM] = new_value


def render_region_picker(valid_ids, labels) -> None:
    """Pills mode ONLY -- renders nothing in links mode. Follows `filters.py`'s
    persistent-key/widget-key split (Wave-0 spike A5, 03-01-SUMMARY.md finding (e)): WIDGET_KEY
    is seeded from PERSIST_KEY before the widget is created, never read as the source of truth
    itself."""
    if REGION_CLICK_MODE != "pills":
        return
    if WIDGET_KEY not in st.session_state:
        st.session_state[WIDGET_KEY] = st.session_state.get(PERSIST_KEY)
    st.pills(
        "Body region",
        options=list(valid_ids),
        format_func=labels.get,
        key=WIDGET_KEY,
        on_change=_sync_widget_to_persist,
    )


def render_clear_control(labels) -> None:
    """Renders the "Showing supporting data for: {label}." caption plus a "Clear region" button
    only when a region is currently selected; renders nothing otherwise -- the page owns the
    no-region copy."""
    region_id = selected_region(set(labels))
    if region_id is None:
        return
    label = html.escape(labels[region_id])
    st.caption(f"Showing supporting data for: {label}.")
    st.button("Clear region", on_click=clear_region, key="clear_body_map_region")
